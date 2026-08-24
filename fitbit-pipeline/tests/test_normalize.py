from tests.conftest import load_fixture

from fitbit_pipeline import normalize


def rows_for(data_type: str, payload_key: str, fixture: str):
    page = load_fixture(fixture)
    rows = []
    for point in page["dataPoints"]:
        rows.extend(normalize.normalize_point(data_type, payload_key, point))
    return rows


def test_timestamps_normalize_to_one_canonical_spelling():
    # Two spellings of the same instant must not become two primary keys.
    assert normalize.iso_utc("2026-08-17T13:00:00Z") == "2026-08-17T13:00:00Z"
    assert normalize.iso_utc("2026-08-17T08:00:00-05:00") == "2026-08-17T13:00:00Z"
    assert normalize.iso_utc(None) is None


def test_local_date_falls_back_to_the_utc_offset():
    # 01:30 UTC at offset -05:00 is still the previous local day.
    assert normalize.local_date_from("2026-08-18T01:30:00Z", "-18000s") == "2026-08-17"


def test_steps_become_interval_rows():
    rows = rows_for("steps", "steps", "steps.json")
    assert len(rows) == 2
    first = rows[0]
    assert first.table == "activity_intervals"
    assert first.values["value"] == 412.0
    assert first.values["unit"] == "count"
    assert first.values["local_date"] == "2026-08-17"
    assert first.keys == ("data_type", "start_time", "subtype", "source_key")


def test_heart_rate_keeps_every_sample_and_its_context():
    rows = rows_for("heart-rate", "heartRate", "heart_rate.json")
    assert [row.values["bpm"] for row in rows] == [62, 118]
    assert rows[1].values["motion_context"] == "ACTIVE"
    assert rows[0].values["sample_time"] == "2026-08-17T13:00:00Z"


def test_active_zone_minutes_keep_their_zone():
    rows = rows_for("active-zone-minutes", "activeZoneMinutes", "active_zone_minutes.json")
    zones = {row.values["subtype"]: row.values["value"] for row in rows}
    assert zones == {"FAT_BURN": 1.0, "CARDIO": 2.0}


def test_sleep_is_reported_on_the_morning_it_ended():
    rows = rows_for("sleep", "sleep", "sleep.json")
    session = next(row for row in rows if row.table == "sleep_sessions")
    # Sleep started the evening of the 16th and ended the morning of the 17th.
    assert session.values["start_time"] == "2026-08-17T04:12:00Z"
    assert session.values["local_date"] == "2026-08-17"
    assert session.values["is_main_sleep"] == 1
    assert session.values["minutes_asleep"] == 442.0
    assert session.values["minutes_deep"] == 70.0

    stages = [row for row in rows if row.table == "sleep_stages"]
    assert len(stages) == 5
    assert sum(stage.values["duration_seconds"] for stage in stages) == 456 * 60


def test_sleep_stage_minutes_are_derived_when_the_summary_is_missing():
    page = load_fixture("sleep.json")
    payload = page["dataPoints"][0]["sleep"]
    del payload["summary"]
    rows = normalize.normalize_point("sleep", "sleep", page["dataPoints"][0])
    session = next(row for row in rows if row.table == "sleep_sessions")
    assert session.values["minutes_deep"] == 70.0
    assert session.values["minutes_light"] == 302.0
    assert session.values["minutes_awake"] == 14.0
    assert session.values["minutes_asleep"] == 442.0


def test_exercise_summary_metrics_survive():
    rows = rows_for("exercise", "exercise", "exercise.json")
    workout = rows[0].values
    assert workout["exercise_id"] == "ex-991"
    assert workout["exercise_type"] == "RUNNING"
    assert workout["distance_mm"] == 8046720
    assert workout["active_duration_seconds"] == 2705.0
    assert workout["duration_seconds"] == 46 * 60
    assert workout["average_hr_bpm"] == 148


def test_daily_types_key_on_the_calendar_date():
    rhr = rows_for("daily-resting-heart-rate", "dailyRestingHeartRate", "daily_resting_heart_rate.json")
    assert rhr[0].values == {
        "date": "2026-08-17",
        "bpm": 54,
        "calculation_method": "WITH_SLEEP",
    }
    hrv = rows_for("daily-heart-rate-variability", "dailyHeartRateVariability", "daily_heart_rate_variability.json")
    assert hrv[0].values["avg_rmssd_ms"] == 41.7
    assert hrv[0].values["non_rem_hr_bpm"] == 56.0


def test_large_integers_arriving_as_strings_are_coerced():
    assert normalize.to_number("1180") == 1180.0
    assert normalize.to_int("54") == 54
    assert normalize.to_number(None) is None
    assert normalize.to_number("") is None
    assert normalize.to_number("not a number") is None


def test_reconciled_points_get_an_empty_source_key():
    # Reconciliation is the act of merging sources, so there is no dataSource.
    assert normalize.source_key({"steps": {}}) == ""
    keyed = normalize.source_key(
        {"dataSource": {"platform": "FITBIT", "recordingMethod": "PASSIVELY_MEASURED"}}
    )
    assert keyed == "FITBIT|PASSIVELY_MEASURED"


def test_a_malformed_point_does_not_abort_the_sync():
    assert normalize.normalize_point("steps", "steps", {"steps": None}) == []
    assert normalize.normalize_point("steps", "steps", {}) == []
    # No interval means no usable row, but no exception either.
    assert normalize.normalize_point("steps", "steps", {"steps": {"count": "5"}}) == []
