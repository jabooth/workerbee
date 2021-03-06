# -*- coding: UTF-8 -*-
import arrow

PERIODS = [
    ('yr',  60*60*24*365),
    ('mth', 60*60*24*30),
    ('day', 60*60*24),
    ('hr',  60*60),
    ('min', 60),
    ('sec', 1)
]

STATS_QUERY = r"""
WITH
    secs AS (
        SELECT EXTRACT (epoch FROM job_duration) AS job_duration_secs
        FROM {tbl_name}
    ),
    stats AS (
        SELECT
            AVG(job_duration_secs) AS mean_duration,
            STDDEV_SAMP(job_duration_secs) AS stddev_duration
        FROM secs
    ),
    bounds AS (
        SELECT (mean_duration - stddev_duration * 2) as lower_bound,
               (mean_duration + stddev_duration * 2) as upper_bound
        FROM stats
    ),
    stats_trimmed AS (
        SELECT AVG(job_duration_secs) as mean_duration_trimmed
        FROM secs
        WHERE job_duration_secs BETWEEN (SELECT lower_bound FROM bounds) AND (SELECT upper_bound FROM bounds)
    ),
    time_windows AS (
        SELECT
            (mean_duration_trimmed + 3 * stddev_duration) * interval '1 second' AS lookback_window
        FROM stats_trimmed, stats
    ),
    recent_finishes AS (
        SELECT
            COUNT(*) AS n_completed_in_window
        FROM {tbl_name}
    WHERE
            time_last_completed > NOW() - (SELECT lookback_window FROM time_windows)
    ),
    rates AS (
        SELECT
            COALESCE(n_completed_in_window / EXTRACT (epoch FROM lookback_window), 0) AS jobs_per_sec
        FROM recent_finishes, time_windows
    ),
    finished AS (
        SELECT
            COUNT(*) AS n_completed
        FROM {tbl_name}
        WHERE
            time_last_completed IS NOT NULL
    ),
    remaining AS (
        SELECT
            COUNT(*) AS n_remaining
        FROM {tbl_name}
        WHERE
            time_last_completed IS NULL
    ),
    etas AS (
        SELECT
            n_remaining / NULLIF(jobs_per_sec, 0) AS secs_to_go
        FROM remaining, rates
    )
SELECT *, NOW() + (secs_to_go * interval '1 second') as finish_time
FROM stats, stats_trimmed, time_windows, recent_finishes, finished, remaining, rates, etas
""".strip()


def get_stats(db_handle, tbl_name):
    return db_handle.one(STATS_QUERY.format(tbl_name=tbl_name))


def seconds_format(seconds, if_none='-'):
    if seconds is None:
        return if_none

    strings = []
    seconds = int(seconds)
    for period_name, period_seconds in PERIODS:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            if period_value == 1:
                strings.append("%s %s" % (period_value, period_name))
            else:
                strings.append("%s %ss" % (period_value, period_name))

    return ", ".join(strings) if len(strings) > 0 else '< 1 second'


def seconds_unit(seconds):
    if seconds is None:
        return PERIODS[-1]  # default to seconds
    for period_name, period_seconds in PERIODS:
        if seconds >= period_seconds:
            return period_name, period_seconds
    return PERIODS[-1]


def percent_str(a, b):
    return "{:.2%}".format(a / b) if b != 0 else '-%'


def stats_to_str(s):
    n_jobs = s.n_remaining + s.n_completed
    period_str, period_secs = seconds_unit(s.mean_duration_trimmed)
    return [
        ("jobs"        , "{}".format(n_jobs)),
        ("completed"   , "{} ({})".format(s.n_completed,
                                          percent_str(s.n_completed, n_jobs))),
        ("av. duration", "{}".format(seconds_format(s.mean_duration_trimmed)))
    ] + ([
        ("jobs / {}".format(period_str), "{:.2f}".format(s.jobs_per_sec * period_secs)),
        ("remaining"                   , "{}".format(seconds_format(s.secs_to_go, if_none='∞'))),
        ("finishes"                    , "{}".format(arrow.get(s.finish_time).humanize()
                                                     if s.finish_time is not None else
                                                     'at heat death of universe'))
    ] if s.n_remaining > 0 else [])


def get_stats_report(db_handle, tbl_name):
    s = get_stats(db_handle, tbl_name)
    return stats_to_str(s), s
