from datetime import date

import pytest

from fitbit_pipeline import datatypes


def test_every_type_has_a_normalizer():
    from fitbit_pipeline.normalize import NORMALIZERS

    missing = [dt.api_id for dt in datatypes.DATA_TYPES if dt.api_id not in NORMALIZERS]
    assert missing == []


def test_filter_uses_the_right_time_field_per_model():
    start, end = date(2026, 8, 1), date(2026, 8, 8)
    assert datatypes.BY_ID["steps"].build_filter(start, end) == (
        'steps.interval.civil_start_time >= "2026-08-01" AND '
        'steps.interval.civil_start_time < "2026-08-08"'
    )
    assert datatypes.BY_ID["heart-rate"].build_filter(start, end) == (
        'heart_rate.sample_time.civil_time >= "2026-08-01" AND '
        'heart_rate.sample_time.civil_time < "2026-08-08"'
    )
    assert datatypes.BY_ID["daily-resting-heart-rate"].build_filter(start, end) == (
        'daily_resting_heart_rate.date >= "2026-08-01" AND '
        'daily_resting_heart_rate.date < "2026-08-08"'
    )
    # A night belongs to the morning, so sleep filters on end time.
    assert "sleep.interval.civil_end_time" in datatypes.BY_ID["sleep"].build_filter(start, end)
    # ECG supports a lower bound only, in physical time.
    ecg = datatypes.BY_ID["electrocardiogram"].build_filter(start, end)
    assert ecg == 'electrocardiogram.interval.start_time >= "2026-08-01T00:00:00Z"'
    assert "AND" not in ecg


def test_page_size_respects_the_documented_caps():
    # Discovery: exercise and sleep cap at 25, everything else at 10000.
    assert datatypes.BY_ID["sleep"].page_size == 25
    assert datatypes.BY_ID["exercise"].page_size == 25
    assert all(dt.page_size <= 10000 for dt in datatypes.DATA_TYPES)


def test_optional_scopes_gate_their_data_types():
    default = {dt.api_id for dt in datatypes.enabled_data_types()}
    assert "electrocardiogram" not in default
    assert "irregular-rhythm-notification" not in default

    widened = {dt.api_id for dt in datatypes.enabled_data_types(["ecg", "irn"])}
    assert "electrocardiogram" in widened
    assert "irregular-rhythm-notification" in widened


def test_skip_list_is_honored():
    selected = {dt.api_id for dt in datatypes.enabled_data_types(skip=["steps"])}
    assert "steps" not in selected
    assert "distance" in selected


def test_unknown_type_names_the_known_ones():
    with pytest.raises(KeyError, match="steps"):
        datatypes.resolve("not-a-type")
