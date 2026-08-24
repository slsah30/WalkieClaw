"""Report queries.

Everything the dashboard shows comes from here, so any number on a page can be
traced to one SQL statement. Values stay in canonical API units; conversion to
imperial happens in the templates through fitbit_pipeline.units.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

# Metrics offered by the trends and correlations views. The key is what appears
# in a URL, so it stays stable; the column is what the SQL reads.
METRICS: dict[str, dict[str, Any]] = {
    "steps": {"label": "Steps", "column": "steps", "unit": "steps"},
    "resting_hr": {"label": "Resting heart rate", "column": "resting_hr", "unit": "bpm"},
    "hrv": {"label": "HRV (RMSSD)", "column": "hrv_rmssd_ms", "unit": "ms"},
    "sleep_minutes": {
        "label": "Sleep duration",
        "column": "sleep_minutes_asleep",
        "unit": "minutes",
    },
    "sleep_efficiency": {
        "label": "Sleep efficiency",
        "column": "sleep_efficiency",
        "unit": "percent",
    },
    "weight": {"label": "Weight", "column": "weight_grams", "unit": "grams"},
    "distance": {"label": "Distance", "column": "distance_mm", "unit": "mm"},
    "azm": {"label": "Active zone minutes", "column": "azm_total", "unit": "minutes"},
    "calories": {
        "label": "Calories burned",
        "column": "calories_total_kcal",
        "unit": "kcal",
    },
    "spo2": {"label": "SpO2", "column": "spo2_avg_percent", "unit": "percent"},
    "breathing_rate": {
        "label": "Breathing rate",
        "column": "breathing_rate",
        "unit": "breaths/min",
    },
    "skin_temp_delta": {
        "label": "Skin temperature variation",
        "column": "skin_temp_delta_c",
        "unit": "celsius_delta",
    },
    "resting_hr_next_day": {
        "label": "Resting heart rate",
        "column": "resting_hr",
        "unit": "bpm",
    },
}


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def date_range(days: int, end: date | None = None) -> tuple[str, str]:
    last = end or date.today()
    first = last - timedelta(days=days - 1)
    return first.isoformat(), last.isoformat()


# --- coverage and status --------------------------------------------------


def database_span(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    row = conn.execute("SELECT MIN(date) AS lo, MAX(date) AS hi FROM daily_summary").fetchone()
    return (row["lo"], row["hi"]) if row else (None, None)


def latest_day_with_data(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT date FROM daily_summary "
        "WHERE steps IS NOT NULL OR sleep_minutes_asleep IS NOT NULL "
        "ORDER BY date DESC LIMIT 1"
    ).fetchone()
    return row["date"] if row else None


def sync_status(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute("SELECT * FROM sync_state ORDER BY data_type")
    )


def recent_runs(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,))
    )


# --- view 1: daily summary ------------------------------------------------


def daily_summary(conn: sqlite3.Connection, day: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM daily_summary WHERE date = ?", (day,)).fetchone()
    return dict(row) if row else None


def neighbouring_days(conn: sqlite3.Connection, day: str) -> tuple[str | None, str | None]:
    previous = conn.execute(
        "SELECT date FROM daily_summary WHERE date < ? ORDER BY date DESC LIMIT 1", (day,)
    ).fetchone()
    following = conn.execute(
        "SELECT date FROM daily_summary WHERE date > ? ORDER BY date LIMIT 1", (day,)
    ).fetchone()
    return (previous["date"] if previous else None, following["date"] if following else None)


def sleep_sessions_for(conn: sqlite3.Connection, day: str) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute(
            "SELECT * FROM sleep_sessions WHERE local_date = ? "
            "ORDER BY is_main_sleep DESC, start_time",
            (day,),
        )
    )


def sleep_stages_for(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute(
            # The stage rows have no offset of their own, so carry the parent
            # session's through for local clock rendering.
            "SELECT stage.*, session.start_utc_offset AS utc_offset "
            "FROM sleep_stages AS stage "
            "JOIN sleep_sessions AS session USING (session_id) "
            "WHERE stage.session_id = ? AND stage.is_short_awakening = 0 "
            "ORDER BY stage.start_time",
            (session_id,),
        )
    )


def workouts_for(conn: sqlite3.Connection, day: str) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute("SELECT * FROM workouts WHERE local_date = ? ORDER BY start_time", (day,))
    )


def azm_breakdown(conn: sqlite3.Connection, day: str) -> dict[str, float]:
    rows = conn.execute(
        "SELECT subtype, SUM(value) AS total FROM activity_intervals "
        "WHERE local_date = ? AND data_type = 'active-zone-minutes' GROUP BY subtype",
        (day,),
    )
    return {row["subtype"]: row["total"] or 0.0 for row in rows}


# --- view 2: trends -------------------------------------------------------


def metric_series(
    conn: sqlite3.Connection, metric: str, start: str, end: str
) -> list[tuple[str, float | None]]:
    column = METRICS[metric]["column"]
    rows = conn.execute(
        f"SELECT date, {column} AS value FROM daily_summary "
        "WHERE date BETWEEN ? AND ? ORDER BY date",
        (start, end),
    )
    return [(row["date"], row["value"]) for row in rows]


def rolling_average(
    series: Sequence[tuple[str, float | None]], window: int
) -> list[tuple[str, float | None]]:
    """Trailing mean over `window` days, ignoring gaps.

    Days with no reading are skipped rather than treated as zero, because a
    watch left on the charger is missing data, not a day of no heartbeat.
    """
    out: list[tuple[str, float | None]] = []
    for index, (day, _value) in enumerate(series):
        window_values = [
            value for _, value in series[max(0, index - window + 1) : index + 1] if value is not None
        ]
        out.append((day, sum(window_values) / len(window_values) if window_values else None))
    return out


def trend_summary(series: Sequence[tuple[str, float | None]]) -> dict[str, Any]:
    values = [value for _, value in series if value is not None]
    if not values:
        return {"count": 0, "latest": None, "mean": None, "min": None, "max": None, "change": None}
    recent = values[-7:]
    earlier = values[:-7][-7:] if len(values) > 7 else []
    change = None
    if recent and earlier:
        change = sum(recent) / len(recent) - sum(earlier) / len(earlier)
    return {
        "count": len(values),
        "latest": values[-1],
        "mean": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
        "change": change,
    }


# --- view 3: sleep --------------------------------------------------------


def sleep_nights(conn: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    """One row per night, main sleep only, newest last."""
    return rows_to_dicts(
        conn.execute(
            """
            SELECT s.*
              FROM sleep_sessions s
              JOIN (
                    SELECT local_date, MIN(CASE WHEN is_main_sleep = 1 THEN 0 ELSE 1 END) AS rank
                      FROM sleep_sessions
                     WHERE local_date BETWEEN ? AND ?
                     GROUP BY local_date
                   ) best
                ON best.local_date = s.local_date
               AND best.rank = CASE WHEN s.is_main_sleep = 1 THEN 0 ELSE 1 END
             WHERE s.local_date BETWEEN ? AND ?
             GROUP BY s.local_date
             ORDER BY s.local_date
            """,
            (start, end, start, end),
        )
    )


def parse_utc_offset(utc_offset: str | None) -> float:
    """Seconds of UTC offset from Google's "-14400s" form. Unparseable means 0."""
    if not utc_offset:
        return 0.0
    text = str(utc_offset)
    if text.endswith("s"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return 0.0


def local_clock(timestamp: str | None, utc_offset: str | None) -> str:
    """Render a stored UTC timestamp as local HH:MM using its own stored offset.

    Reports are for one person in one place, so a bedtime has to read as the
    time they saw on the clock. Slicing the ISO string instead shows UTC, which
    is off by the whole offset and silently wrong across a DST change.
    """
    if not timestamp:
        return "n/a"
    try:
        moment = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return "n/a"
    return (moment + timedelta(seconds=parse_utc_offset(utc_offset))).strftime("%H:%M")


def clock_position(timestamp: str | None, utc_offset: str | None) -> float | None:
    """Local clock hour of a timestamp, shifted so an evening bedtime is negative.

    Bedtimes straddle midnight. Plotting 23:40 as 23.7 and 00:20 as 0.3 would
    put two almost identical nights at opposite ends of the axis, so anything
    after noon becomes a negative offset from midnight.
    """
    if not timestamp:
        return None
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    local = moment + timedelta(seconds=parse_utc_offset(utc_offset))
    hour = local.hour + local.minute / 60.0
    return hour - 24.0 if hour >= 12 else hour


def sleep_consistency(nights: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for night in nights:
        out.append(
            {
                "date": night["local_date"],
                "bedtime": clock_position(night["start_time"], night["start_utc_offset"]),
                "waketime": clock_position(night["end_time"], night["end_utc_offset"]),
                "minutes_asleep": night["minutes_asleep"],
            }
        )
    return out


def sleep_weekly_averages(conn: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute(
            """
            SELECT strftime('%Y-W%W', local_date) AS week,
                   MIN(local_date) AS week_start,
                   COUNT(*) AS nights,
                   AVG(minutes_asleep) AS minutes_asleep,
                   AVG(minutes_deep) AS minutes_deep,
                   AVG(minutes_light) AS minutes_light,
                   AVG(minutes_rem) AS minutes_rem,
                   AVG(minutes_awake) AS minutes_awake,
                   AVG(CASE WHEN minutes_in_period > 0
                            THEN 100.0 * minutes_asleep / minutes_in_period END) AS efficiency
              FROM sleep_sessions
             WHERE local_date BETWEEN ? AND ? AND is_nap = 0
             GROUP BY week
             ORDER BY week
            """,
            (start, end),
        )
    )


# --- view 4: intraday heart rate ------------------------------------------


def heartrate_samples(conn: sqlite3.Connection, day: str) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute(
            "SELECT sample_time, bpm, motion_context, utc_offset FROM heartrate_intraday "
            "WHERE local_date = ? ORDER BY sample_time",
            (day,),
        )
    )


def heartrate_zones(conn: sqlite3.Connection, day: str) -> list[dict[str, Any]]:
    """Zone thresholds for the day, falling back to the most recent known set."""
    rows = rows_to_dicts(
        conn.execute(
            "SELECT zone, min_bpm, max_bpm FROM hr_zones_daily WHERE date = ? ORDER BY min_bpm",
            (day,),
        )
    )
    if rows:
        return rows
    latest = conn.execute(
        "SELECT date FROM hr_zones_daily WHERE date <= ? ORDER BY date DESC LIMIT 1", (day,)
    ).fetchone()
    if not latest:
        return []
    return rows_to_dicts(
        conn.execute(
            "SELECT zone, min_bpm, max_bpm FROM hr_zones_daily WHERE date = ? ORDER BY min_bpm",
            (latest["date"],),
        )
    )


def heartrate_granularity(samples: Sequence[dict[str, Any]]) -> str:
    """Describe the actual sample spacing, so the view never overstates it."""
    if len(samples) < 2:
        return "not enough samples"
    gaps = []
    for earlier, later in zip(samples, samples[1:]):
        try:
            first = datetime.fromisoformat(earlier["sample_time"].replace("Z", "+00:00"))
            second = datetime.fromisoformat(later["sample_time"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        gap = (second - first).total_seconds()
        if 0 < gap <= 3600:
            gaps.append(gap)
    if not gaps:
        return "irregular"
    gaps.sort()
    median = gaps[len(gaps) // 2]
    if median < 60:
        return f"{median:.0f} second samples"
    return f"{median / 60:.0f} minute samples"


# --- view 5: correlations -------------------------------------------------


def pearson(pairs: Sequence[tuple[float, float]]) -> float | None:
    n = len(pairs)
    if n < 3:
        return None
    mean_x = sum(x for x, _ in pairs) / n
    mean_y = sum(y for _, y in pairs) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    denominator_x = sum((x - mean_x) ** 2 for x, _ in pairs) ** 0.5
    denominator_y = sum((y - mean_y) ** 2 for _, y in pairs) ** 0.5
    if denominator_x == 0 or denominator_y == 0:
        return None
    return numerator / (denominator_x * denominator_y)


def correlation_pairs(
    conn: sqlite3.Connection,
    x_metric: str,
    y_metric: str,
    start: str,
    end: str,
    lag_days: int = 0,
) -> list[dict[str, Any]]:
    """Pair two daily metrics, optionally offsetting y by `lag_days`.

    A lag of 1 answers questions of the shape "does what I did today show up in
    tomorrow's number", for example prior day steps against tonight's sleep.
    """
    x_column = METRICS[x_metric]["column"]
    y_column = METRICS[y_metric]["column"]
    rows = conn.execute(
        f"""
        SELECT a.date AS date, a.{x_column} AS x, b.{y_column} AS y, b.date AS y_date
          FROM daily_summary a
          JOIN daily_summary b
            ON b.date = date(a.date, ?)
         WHERE a.date BETWEEN ? AND ?
           AND a.{x_column} IS NOT NULL
           AND b.{y_column} IS NOT NULL
         ORDER BY a.date
        """,
        (f"+{lag_days} days", start, end),
    )
    return rows_to_dicts(rows)


def linear_fit(pairs: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    """Least squares slope and intercept, for the trend line on the scatter."""
    n = len(pairs)
    if n < 3:
        return None
    mean_x = sum(x for x, _ in pairs) / n
    mean_y = sum(y for _, y in pairs) / n
    denominator = sum((x - mean_x) ** 2 for x, _ in pairs)
    if denominator == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in pairs) / denominator
    return slope, mean_y - slope * mean_x
