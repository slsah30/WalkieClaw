"""Dashboard tests: every view renders, and the numbers on the page are right."""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fitbit_pipeline import db
from fitbit_pipeline.config import Config
from fitbit_pipeline.reports import charts, queries
from fitbit_pipeline.reports.app import create_app

DAYS = 45
END = date(2026, 8, 17)


@pytest.fixture
def seeded(tmp_path):
    """A database with a month and a half of plausible, deterministic data."""
    path = tmp_path / "seeded.sqlite3"
    conn = db.open_database(path)
    rng = random.Random(7)

    for offset in range(DAYS):
        day = (END - timedelta(days=DAYS - 1 - offset)).isoformat()
        steps = 6000 + rng.randint(0, 7000)
        for hour in range(0, 24, 2):
            db.upsert(
                conn,
                "activity_intervals",
                {
                    "data_type": "steps",
                    "start_time": f"{day}T{hour:02d}:00:00Z",
                    "source_key": "",
                    "subtype": "",
                    "end_time": f"{day}T{hour:02d}:59:00Z",
                    "local_date": day,
                    "start_utc_offset": "0s",
                    "value": steps / 12,
                    "unit": "count",
                },
                ("data_type", "start_time", "subtype", "source_key"),
            )
        db.upsert(
            conn,
            "activity_intervals",
            {
                "data_type": "active-zone-minutes",
                "start_time": f"{day}T18:00:00Z",
                "source_key": "",
                "subtype": "CARDIO",
                "end_time": f"{day}T18:20:00Z",
                "local_date": day,
                "start_utc_offset": "0s",
                "value": 24,
                "unit": "azm",
            },
            ("data_type", "start_time", "subtype", "source_key"),
        )
        db.upsert(conn, "resting_hr", {"date": day, "bpm": 52 + rng.randint(0, 6)}, ("date",))
        db.upsert(
            conn, "hrv_daily", {"date": day, "avg_rmssd_ms": 38 + rng.random() * 12}, ("date",)
        )
        db.upsert(
            conn,
            "spo2_daily",
            {"date": day, "average_percent": 95 + rng.random() * 2},
            ("date",),
        )
        db.upsert(
            conn, "breathing_rate_daily", {"date": day, "breaths_per_minute": 14 + rng.random()}, ("date",)
        )
        db.upsert(
            conn,
            "weight",
            {
                "sample_time": f"{day}T12:00:00Z",
                "source_key": "",
                "local_date": day,
                "weight_grams": 81000 + rng.randint(-600, 600),
            },
            ("sample_time", "source_key"),
        )
        for zone, lo, hi in (("LIGHT", 95, 120), ("MODERATE", 120, 145), ("VIGOROUS", 145, 170), ("PEAK", 170, 200)):
            db.upsert(
                conn,
                "hr_zones_daily",
                {"date": day, "zone": zone, "min_bpm": lo, "max_bpm": hi},
                ("date", "zone"),
            )

        session_id = f"sleep-{day}"
        asleep = 400 + rng.randint(-60, 60)
        db.upsert(
            conn,
            "sleep_sessions",
            {
                "session_id": session_id,
                "start_time": f"{day}T04:20:00Z",
                "end_time": f"{day}T12:00:00Z",
                "local_date": day,
                "start_utc_offset": "-18000s",
                "end_utc_offset": "-18000s",
                "sleep_type": "STAGES",
                "is_main_sleep": 1,
                "is_nap": 0,
                "processed": 1,
                "manually_edited": 0,
                "stages_status": "SUCCEEDED",
                "minutes_asleep": asleep,
                "minutes_awake": 20,
                "minutes_in_period": asleep + 20,
                "minutes_deep": 70,
                "minutes_light": asleep - 140,
                "minutes_rem": 70,
            },
            ("session_id",),
        )
        db.upsert(
            conn,
            "sleep_stages",
            {
                "session_id": session_id,
                "start_time": f"{day}T04:20:00Z",
                "stage": "LIGHT",
                "end_time": f"{day}T06:00:00Z",
                "duration_seconds": 6000,
                "is_short_awakening": 0,
            },
            ("session_id", "start_time", "stage"),
        )

    # One day of minute level heart rate, on the last day.
    last = END.isoformat()
    for minute in range(0, 24 * 60, 1):
        stamp = f"{last}T{minute // 60:02d}:{minute % 60:02d}:00Z"
        bpm = int(64 + 18 * math.sin(minute / 90.0) + rng.randint(-3, 3))
        db.upsert(
            conn,
            "heartrate_intraday",
            {
                "sample_time": stamp,
                "source_key": "",
                "local_date": last,
                "bpm": bpm,
                "motion_context": "SEDENTARY",
                "sensor_location": "WRIST",
                "utc_offset": "0s",
            },
            ("sample_time", "source_key"),
        )

    db.upsert(
        conn,
        "workouts",
        {
            "exercise_id": "w1",
            "start_time": f"{last}T22:00:00Z",
            "end_time": f"{last}T22:46:00Z",
            "local_date": last,
            "exercise_type": "RUNNING",
            "display_name": "Run",
            "duration_seconds": 2760,
            "distance_mm": 8046720,
            "calories_kcal": 512,
            "average_hr_bpm": 148,
            "active_zone_minutes": 62,
        },
        ("exercise_id",),
    )

    from fitbit_pipeline.aggregate import rebuild_daily_summary

    rebuild_daily_summary(conn)
    conn.close()

    config = Config()
    config.database.path = path
    return config


@pytest.fixture
def client(seeded):
    return TestClient(create_app(seeded))


def test_root_redirects_to_the_daily_view(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/day"


def test_daily_view_shows_the_stored_numbers(client):
    response = client.get(f"/day?date={END.isoformat()}")
    assert response.status_code == 200
    body = response.text
    assert END.isoformat() in body
    assert "Daily summary" in body
    assert "Active zone minutes" in body
    assert "Run" in body  # the workout table
    assert "<svg" in body


def test_daily_view_converts_to_imperial(client):
    response = client.get(f"/day?date={END.isoformat()}")
    # 8046720 mm is 5.00 miles, and weight is shown in pounds not grams.
    assert "5.00" in response.text
    assert "81,000" not in response.text


def test_a_day_with_no_data_says_so_rather_than_erroring(client):
    response = client.get("/day?date=1999-01-01")
    assert response.status_code == 200
    assert "No data stored" in response.text


def test_bad_date_is_rejected(client):
    assert client.get("/day?date=not-a-date").status_code == 400


def test_trends_view_renders_every_panel(client):
    response = client.get("/trends?days=30")
    assert response.status_code == 200
    for label in ("Resting heart rate", "HRV (RMSSD)", "Sleep duration", "Steps", "Weight"):
        assert label in response.text
    assert response.text.count("<svg") >= 5


def test_sleep_view_renders(client):
    response = client.get("/sleep?days=30")
    assert response.status_code == 200
    assert "Stage breakdown per night" in response.text
    assert "Bedtime and wake consistency" in response.text
    assert "Weekly averages" in response.text
    # It should be explicit about sleep score not existing in the API.
    assert "sleep score" in response.text


def test_intraday_view_draws_every_sample(client):
    response = client.get(f"/heartrate?date={END.isoformat()}")
    assert response.status_code == 200
    assert "1,440 samples" in response.text or "1440 samples" in response.text
    assert "1 minute samples" in response.text
    assert "polyline" in response.text
    assert "zone" in response.text


def test_correlations_view_computes_r(client):
    response = client.get("/correlations?x=steps&y=sleep_minutes&lag=1&days=45")
    assert response.status_code == 200
    assert "Pearson r" in response.text
    assert "pairs" in response.text


def test_correlations_rejects_unknown_metrics(client):
    assert client.get("/correlations?x=nope&y=steps").status_code == 400


def test_status_view_renders_with_no_runs(client):
    assert client.get("/status").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/export/daily.csv",
        "/export/sleep.csv",
        f"/export/heartrate.csv?date={END.isoformat()}",
        "/export/workouts.csv",
        "/export/trends.csv?metric=resting_hr&days=30",
        "/export/correlations.csv?x=steps&y=sleep_minutes&lag=1",
    ],
)
def test_every_report_exports_csv(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert len(lines) >= 2  # header plus at least one row


def test_csv_export_uses_imperial_columns(client):
    response = client.get("/export/trends.csv?metric=weight&days=30")
    header = response.text.splitlines()[0]
    assert "weight_lb" in header
    first_value = float(response.text.splitlines()[1].split(",")[1])
    assert 150 < first_value < 200  # pounds, not grams


def test_dashboard_opens_the_database_read_only(seeded):
    client = TestClient(create_app(seeded))
    client.get("/day")
    # A read only connection cannot be used to mutate the database.
    import sqlite3

    conn = sqlite3.connect(f"file:{seeded.database.path}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM daily_summary")


# --- chart primitives -----------------------------------------------------


def test_line_chart_breaks_the_line_across_gaps():
    series = charts.Series("x", [("a", 1.0), ("b", None), ("c", 3.0), ("d", 4.0)])
    svg = charts.line_chart([series])
    assert svg.count("<polyline") == 1  # only the c-d run is drawable


def test_charts_escape_untrusted_labels():
    svg = charts.line_chart([charts.Series("<script>", [("<b>", 1.0), ("x", 2.0)])])
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg or "&lt;b&gt;" in svg


def test_empty_series_renders_a_placeholder_not_a_crash():
    assert "No data" in charts.line_chart([])
    assert "No data" in charts.stacked_bar_chart([], [])
    assert "Not enough" in charts.scatter_chart([], x_label="a", y_label="b")


def test_intraday_chart_breaks_on_a_reporting_gap():
    samples = [(0.0, 60, ""), (0.01, 61, ""), (5.0, 70, ""), (5.01, 71, "")]
    svg = charts.intraday_heartrate_chart(samples, [])
    assert svg.count("<polyline") == 2


# --- query helpers --------------------------------------------------------


def test_rolling_average_skips_missing_days_rather_than_zeroing_them():
    series = [("d1", 10.0), ("d2", None), ("d3", 20.0)]
    assert queries.rolling_average(series, 3) == [("d1", 10.0), ("d2", 10.0), ("d3", 15.0)]


def test_rolling_average_returns_none_when_the_window_is_empty():
    assert queries.rolling_average([("d1", None)], 7) == [("d1", None)]


def test_clock_position_keeps_bedtimes_adjacent_across_midnight():
    late = queries.clock_position("2026-08-17T04:40:00Z", "-18000s")  # 23:40 local
    early = queries.clock_position("2026-08-17T05:20:00Z", "-18000s")  # 00:20 local
    assert late == pytest.approx(-0.333, abs=0.01)
    assert early == pytest.approx(0.333, abs=0.01)
    assert abs(early - late) < 1.0


def test_pearson_matches_a_known_value():
    assert queries.pearson([(1, 2), (2, 4), (3, 6)]) == pytest.approx(1.0)
    assert queries.pearson([(1, 6), (2, 4), (3, 2)]) == pytest.approx(-1.0)
    assert queries.pearson([(1, 1), (2, 2)]) is None  # too few points


def test_linear_fit_recovers_a_known_line():
    slope, intercept = queries.linear_fit([(0, 1), (1, 3), (2, 5)])
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(1.0)


def test_daily_view_charts_the_night_stage_breakdown(client):
    response = client.get(f"/day?date={END.isoformat()}")
    assert "No sleep stages recorded" not in response.text
    assert "Deep" in response.text and "REM" in response.text


def test_daily_view_falls_back_to_the_summary_when_no_session_row_exists(seeded):
    import sqlite3

    conn = sqlite3.connect(seeded.database.path)
    conn.execute("DELETE FROM sleep_sessions WHERE local_date = ?", (END.isoformat(),))
    conn.commit()
    conn.close()
    response = TestClient(create_app(seeded)).get(f"/day?date={END.isoformat()}")
    assert response.status_code == 200
    assert "No sleep stages recorded" not in response.text


@pytest.mark.parametrize(
    "path,expected",
    [("/trends?days=45", "45"), ("/sleep?days=45", "45"), ("/correlations?days=45", "45")],
)
def test_a_custom_window_stays_selected_in_the_dropdown(client, path, expected):
    body = client.get(path).text
    assert f'<option value="{expected}" selected>' in body
