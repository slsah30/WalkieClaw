"""Rebuild the derived daily_summary table.

daily_summary is a real table rather than a view so the dashboard stays fast
and so ad hoc SQL against the database gets the same numbers the reports show.
It is rebuilt idempotently after every sync: recomputing a day always produces
the same row, and only days touched by the sync are recomputed.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Iterable

from fitbit_pipeline.db import upsert, utc_now

log = logging.getLogger(__name__)

# Tables that carry a local_date or date column, used to discover which days
# exist when rebuilding everything.
DATE_SOURCES = (
    ("activity_intervals", "local_date"),
    ("heartrate_intraday", "local_date"),
    ("resting_hr", "date"),
    ("sleep_sessions", "local_date"),
    ("hrv", "local_date"),
    ("hrv_daily", "date"),
    ("spo2", "local_date"),
    ("spo2_daily", "date"),
    ("breathing_rate_daily", "date"),
    ("skin_temp", "date"),
    ("workouts", "local_date"),
    ("weight", "local_date"),
    ("body_fat", "local_date"),
    ("vo2max_daily", "date"),
)


def all_known_dates(conn: sqlite3.Connection) -> list[str]:
    union = " UNION ".join(
        f"SELECT {column} AS d FROM {table} WHERE {column} IS NOT NULL"
        for table, column in DATE_SOURCES
    )
    return [row["d"] for row in conn.execute(f"SELECT DISTINCT d FROM ({union}) ORDER BY d")]


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def _interval_sum(conn: sqlite3.Connection, day: str, data_type: str, subtype: str | None = None) -> float | None:
    if subtype is None:
        return _scalar(
            conn,
            "SELECT SUM(value) FROM activity_intervals WHERE local_date = ? AND data_type = ?",
            (day, data_type),
        )
    return _scalar(
        conn,
        "SELECT SUM(value) FROM activity_intervals "
        "WHERE local_date = ? AND data_type = ? AND subtype = ?",
        (day, data_type, subtype),
    )


def summarize_day(conn: sqlite3.Connection, day: str) -> dict[str, Any]:
    """Compute one daily_summary row from the normalized tables."""
    steps = _interval_sum(conn, day, "steps")
    distance = _interval_sum(conn, day, "distance")
    floors = _interval_sum(conn, day, "floors")
    active_kcal = _interval_sum(conn, day, "active-energy-burned")
    basal_kcal = _interval_sum(conn, day, "basal-energy-burned")

    azm_fat = _interval_sum(conn, day, "active-zone-minutes", "FAT_BURN")
    azm_cardio = _interval_sum(conn, day, "active-zone-minutes", "CARDIO")
    azm_peak = _interval_sum(conn, day, "active-zone-minutes", "PEAK")
    azm_total = _interval_sum(conn, day, "active-zone-minutes")

    sedentary = _interval_sum(conn, day, "activity-level", "SEDENTARY")
    if sedentary is None:
        sedentary = _interval_sum(conn, day, "sedentary-period")

    hr = conn.execute(
        "SELECT MIN(bpm) AS lo, MAX(bpm) AS hi, AVG(bpm) AS avg, COUNT(*) AS n "
        "FROM heartrate_intraday WHERE local_date = ?",
        (day,),
    ).fetchone()

    spo2_avg = _scalar(conn, "SELECT average_percent FROM spo2_daily WHERE date = ?", (day,))
    if spo2_avg is None:
        spo2_avg = _scalar(conn, "SELECT AVG(percentage) FROM spo2 WHERE local_date = ?", (day,))

    hrv = conn.execute(
        "SELECT avg_rmssd_ms, deep_sleep_rmssd_ms FROM hrv_daily WHERE date = ?", (day,)
    ).fetchone()
    hrv_avg = hrv["avg_rmssd_ms"] if hrv else None
    if hrv_avg is None:
        hrv_avg = _scalar(conn, "SELECT AVG(rmssd_ms) FROM hrv WHERE local_date = ?", (day,))

    skin = conn.execute(
        "SELECT nightly_celsius, baseline_celsius FROM skin_temp WHERE date = ?", (day,)
    ).fetchone()
    skin_delta = None
    if skin and skin["nightly_celsius"] is not None and skin["baseline_celsius"] is not None:
        skin_delta = skin["nightly_celsius"] - skin["baseline_celsius"]

    # The main sleep of the night, falling back to the longest session when the
    # API has not flagged one, which happens before stage processing finishes.
    sleep = conn.execute(
        """
        SELECT * FROM sleep_sessions
         WHERE local_date = ?
         ORDER BY is_main_sleep DESC, COALESCE(minutes_in_period, 0) DESC
         LIMIT 1
        """,
        (day,),
    ).fetchone()

    efficiency = None
    if sleep and sleep["minutes_asleep"] and sleep["minutes_in_period"]:
        efficiency = 100.0 * sleep["minutes_asleep"] / sleep["minutes_in_period"]

    workouts = conn.execute(
        "SELECT COUNT(*) AS n, SUM(duration_seconds) / 60.0 AS minutes "
        "FROM workouts WHERE local_date = ?",
        (day,),
    ).fetchone()

    return {
        "date": day,
        "steps": None if steps is None else int(steps),
        "distance_mm": distance,
        "floors": None if floors is None else int(floors),
        "calories_active_kcal": active_kcal,
        "calories_basal_kcal": basal_kcal,
        # Only a real total. Google returns basal-energy-burned empty for some
        # accounts, and active + 0 presented as "total" silently understates the
        # day by a whole BMR and disagrees with the Fitbit app. Prefer no number.
        "calories_total_kcal": (
            None
            if active_kcal is None or basal_kcal is None
            else active_kcal + basal_kcal
        ),
        "azm_total": None if azm_total is None else int(azm_total),
        "azm_fat_burn": None if azm_fat is None else int(azm_fat),
        "azm_cardio": None if azm_cardio is None else int(azm_cardio),
        "azm_peak": None if azm_peak is None else int(azm_peak),
        "active_minutes_light": _interval_sum(conn, day, "active-minutes", "LIGHT"),
        "active_minutes_moderate": _interval_sum(conn, day, "active-minutes", "MODERATE"),
        "active_minutes_vigorous": _interval_sum(conn, day, "active-minutes", "VIGOROUS"),
        "sedentary_minutes": sedentary,
        "resting_hr": _scalar(conn, "SELECT bpm FROM resting_hr WHERE date = ?", (day,)),
        "hr_min": hr["lo"] if hr else None,
        "hr_max": hr["hi"] if hr else None,
        "hr_avg": hr["avg"] if hr else None,
        "hr_sample_count": hr["n"] if hr else 0,
        "hrv_rmssd_ms": hrv_avg,
        "hrv_deep_sleep_rmssd_ms": hrv["deep_sleep_rmssd_ms"] if hrv else None,
        "spo2_avg_percent": spo2_avg,
        "breathing_rate": _scalar(
            conn, "SELECT breaths_per_minute FROM breathing_rate_daily WHERE date = ?", (day,)
        ),
        "skin_temp_nightly_c": skin["nightly_celsius"] if skin else None,
        "skin_temp_delta_c": skin_delta,
        "sleep_minutes_asleep": sleep["minutes_asleep"] if sleep else None,
        "sleep_minutes_in_period": sleep["minutes_in_period"] if sleep else None,
        "sleep_minutes_deep": sleep["minutes_deep"] if sleep else None,
        "sleep_minutes_light": sleep["minutes_light"] if sleep else None,
        "sleep_minutes_rem": sleep["minutes_rem"] if sleep else None,
        "sleep_minutes_awake": sleep["minutes_awake"] if sleep else None,
        "sleep_efficiency": efficiency,
        "sleep_start_time": sleep["start_time"] if sleep else None,
        "sleep_end_time": sleep["end_time"] if sleep else None,
        "weight_grams": _scalar(
            conn,
            "SELECT weight_grams FROM weight WHERE local_date = ? ORDER BY sample_time DESC LIMIT 1",
            (day,),
        ),
        "body_fat_percent": _scalar(
            conn,
            "SELECT percentage FROM body_fat WHERE local_date = ? ORDER BY sample_time DESC LIMIT 1",
            (day,),
        ),
        "vo2_max": _scalar(conn, "SELECT vo2_max FROM vo2max_daily WHERE date = ?", (day,)),
        "workout_count": workouts["n"] if workouts else 0,
        "workout_minutes": workouts["minutes"] if workouts else None,
        "updated_at": utc_now(),
    }


def rebuild_daily_summary(
    conn: sqlite3.Connection, dates: Iterable[str] | None = None
) -> int:
    """Recompute daily_summary for the given days, or for every known day."""
    days = sorted(set(dates)) if dates is not None else all_known_dates(conn)
    for day in days:
        if not day:
            continue
        upsert(conn, "daily_summary", summarize_day(conn, day), ("date",))
    log.info("daily summary rebuilt", extra={"days": len(days)})
    return len(days)
