-- Initial schema.
--
-- Two layers. raw_payloads keeps every API response verbatim so an upstream
-- schema change can never lose data, and every normalized row can be rebuilt.
-- The normalized tables are what reports query.
--
-- Canonical API units are stored untouched: millimeters, grams, Celsius,
-- kilocalories, minutes. Imperial conversion happens at render time.

CREATE TABLE raw_payloads (
    id            INTEGER PRIMARY KEY,
    data_type     TEXT NOT NULL,
    method        TEXT NOT NULL,            -- reconcile or list
    range_start   TEXT NOT NULL,            -- inclusive, YYYY-MM-DD
    range_end     TEXT NOT NULL,            -- exclusive, YYYY-MM-DD
    page_index    INTEGER NOT NULL,
    fetch_time    TEXT NOT NULL,
    point_count   INTEGER NOT NULL,
    payload_hash  TEXT NOT NULL,
    json          TEXT NOT NULL
);

-- Re-running a sync over an unchanged range is a no-op here. A changed
-- response inserts a new row, so late arriving data keeps its history.
CREATE UNIQUE INDEX raw_payloads_dedupe
    ON raw_payloads (data_type, range_start, range_end, page_index, payload_hash);
CREATE INDEX raw_payloads_by_type_time ON raw_payloads (data_type, range_start);

CREATE TABLE sync_state (
    data_type          TEXT PRIMARY KEY,
    last_success_at    TEXT,
    synced_through     TEXT,   -- newest date confirmed synced
    backfill_start     TEXT,   -- lower bound the backfill is walking toward
    backfill_cursor    TEXT,   -- next chunk end, exclusive; walks backward
    backfill_complete  INTEGER NOT NULL DEFAULT 0,
    earliest_data_date TEXT,   -- oldest date that actually returned data
    total_records      INTEGER NOT NULL DEFAULT 0,
    updated_at         TEXT
);

CREATE TABLE sync_runs (
    id             INTEGER PRIMARY KEY,
    mode           TEXT NOT NULL,           -- backfill, daily, dry-run
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,           -- running, ok, partial, failed
    api_calls      INTEGER NOT NULL DEFAULT 0,
    records        INTEGER NOT NULL DEFAULT 0,
    raw_pages      INTEGER NOT NULL DEFAULT 0,
    range_start    TEXT,
    range_end      TEXT,
    error          TEXT
);

CREATE TABLE sync_run_items (
    run_id      INTEGER NOT NULL REFERENCES sync_runs (id) ON DELETE CASCADE,
    data_type   TEXT NOT NULL,
    status      TEXT NOT NULL,
    api_calls   INTEGER NOT NULL DEFAULT 0,
    records     INTEGER NOT NULL DEFAULT 0,
    method      TEXT,
    error       TEXT,
    PRIMARY KEY (run_id, data_type)
);

-- Interval shaped activity metrics share one table. data_type keeps them
-- apart, subtype carries the zone or level that some of them are bucketed by.
CREATE TABLE activity_intervals (
    data_type        TEXT NOT NULL,
    start_time       TEXT NOT NULL,
    source_key       TEXT NOT NULL DEFAULT '',
    subtype          TEXT NOT NULL DEFAULT '',
    end_time         TEXT,
    local_date       TEXT NOT NULL,
    start_utc_offset TEXT,
    value            REAL,
    unit             TEXT,
    PRIMARY KEY (data_type, start_time, subtype, source_key)
);
CREATE INDEX activity_intervals_by_date ON activity_intervals (local_date, data_type);

CREATE TABLE heartrate_intraday (
    sample_time      TEXT NOT NULL,
    source_key       TEXT NOT NULL DEFAULT '',
    local_date       TEXT NOT NULL,
    bpm              INTEGER NOT NULL,
    motion_context   TEXT,
    sensor_location  TEXT,
    utc_offset       TEXT,
    PRIMARY KEY (sample_time, source_key)
);
CREATE INDEX heartrate_intraday_by_date ON heartrate_intraday (local_date, sample_time);

CREATE TABLE resting_hr (
    date               TEXT PRIMARY KEY,
    bpm                INTEGER NOT NULL,
    calculation_method TEXT
);

CREATE TABLE hr_zones_daily (
    date     TEXT NOT NULL,
    zone     TEXT NOT NULL,
    min_bpm  INTEGER,
    max_bpm  INTEGER,
    PRIMARY KEY (date, zone)
);

CREATE TABLE sleep_sessions (
    session_id             TEXT PRIMARY KEY,
    start_time             TEXT NOT NULL,
    end_time               TEXT NOT NULL,
    local_date             TEXT NOT NULL,   -- civil date of wake, the report day
    start_utc_offset       TEXT,
    end_utc_offset         TEXT,
    sleep_type             TEXT,            -- CLASSIC or STAGES
    is_main_sleep          INTEGER,
    is_nap                 INTEGER,
    processed              INTEGER,
    manually_edited        INTEGER,
    stages_status          TEXT,
    external_id            TEXT,
    minutes_asleep         REAL,
    minutes_awake          REAL,
    minutes_in_period      REAL,
    minutes_to_fall_asleep REAL,
    minutes_after_wakeup   REAL,
    minutes_deep           REAL,
    minutes_light          REAL,
    minutes_rem            REAL,
    minutes_restless       REAL,
    source_key             TEXT DEFAULT ''
);
CREATE INDEX sleep_sessions_by_date ON sleep_sessions (local_date);

CREATE TABLE sleep_stages (
    session_id       TEXT NOT NULL REFERENCES sleep_sessions (session_id) ON DELETE CASCADE,
    start_time       TEXT NOT NULL,
    stage            TEXT NOT NULL,
    end_time         TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    is_short_awakening INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, start_time, stage)
);

CREATE TABLE hrv (
    sample_time TEXT NOT NULL,
    source_key  TEXT NOT NULL DEFAULT '',
    local_date  TEXT NOT NULL,
    rmssd_ms    REAL,
    sdnn_ms     REAL,
    PRIMARY KEY (sample_time, source_key)
);
CREATE INDEX hrv_by_date ON hrv (local_date);

CREATE TABLE hrv_daily (
    date                TEXT PRIMARY KEY,
    avg_rmssd_ms        REAL,
    deep_sleep_rmssd_ms REAL,
    entropy             REAL,
    non_rem_hr_bpm      REAL
);

CREATE TABLE spo2 (
    sample_time TEXT NOT NULL,
    source_key  TEXT NOT NULL DEFAULT '',
    local_date  TEXT NOT NULL,
    percentage  REAL NOT NULL,
    PRIMARY KEY (sample_time, source_key)
);
CREATE INDEX spo2_by_date ON spo2 (local_date);

CREATE TABLE spo2_daily (
    date            TEXT PRIMARY KEY,
    average_percent REAL,
    lower_percent   REAL,
    upper_percent   REAL,
    stddev_percent  REAL
);

CREATE TABLE breathing_rate_daily (
    date              TEXT PRIMARY KEY,
    breaths_per_minute REAL NOT NULL
);

CREATE TABLE breathing_rate_sleep (
    sample_time  TEXT NOT NULL,
    source_key   TEXT NOT NULL DEFAULT '',
    local_date   TEXT NOT NULL,
    full_bpm     REAL,
    deep_bpm     REAL,
    light_bpm    REAL,
    rem_bpm      REAL,
    full_stddev  REAL,
    PRIMARY KEY (sample_time, source_key)
);

CREATE TABLE skin_temp (
    date                       TEXT PRIMARY KEY,
    nightly_celsius            REAL,
    baseline_celsius           REAL,
    relative_stddev_30d_celsius REAL
);

CREATE TABLE workouts (
    exercise_id            TEXT PRIMARY KEY,
    start_time             TEXT NOT NULL,
    end_time               TEXT NOT NULL,
    local_date             TEXT NOT NULL,
    start_utc_offset       TEXT,
    exercise_type          TEXT,
    display_name           TEXT,
    duration_seconds       REAL,
    active_duration_seconds REAL,
    distance_mm            REAL,
    calories_kcal          REAL,
    steps                  INTEGER,
    average_hr_bpm         INTEGER,
    active_zone_minutes    INTEGER,
    elevation_gain_mm      REAL,
    average_speed_mm_s     REAL,
    has_gps                INTEGER,
    notes                  TEXT,
    source_key             TEXT DEFAULT ''
);
CREATE INDEX workouts_by_date ON workouts (local_date);

CREATE TABLE weight (
    sample_time  TEXT NOT NULL,
    source_key   TEXT NOT NULL DEFAULT '',
    local_date   TEXT NOT NULL,
    weight_grams REAL NOT NULL,
    notes        TEXT,
    PRIMARY KEY (sample_time, source_key)
);
CREATE INDEX weight_by_date ON weight (local_date);

CREATE TABLE body_fat (
    sample_time TEXT NOT NULL,
    source_key  TEXT NOT NULL DEFAULT '',
    local_date  TEXT NOT NULL,
    percentage  REAL NOT NULL,
    PRIMARY KEY (sample_time, source_key)
);

CREATE TABLE height (
    sample_time       TEXT NOT NULL,
    source_key        TEXT NOT NULL DEFAULT '',
    local_date        TEXT NOT NULL,
    height_millimeters REAL NOT NULL,
    PRIMARY KEY (sample_time, source_key)
);

CREATE TABLE vo2max_daily (
    date                 TEXT PRIMARY KEY,
    vo2_max              REAL,
    cardio_fitness_level TEXT,
    estimated            INTEGER
);

CREATE TABLE ecg_readings (
    reading_id            TEXT PRIMARY KEY,
    start_time            TEXT NOT NULL,
    end_time              TEXT,
    local_date            TEXT NOT NULL,
    result_classification TEXT,
    average_bpm           INTEGER,
    sampling_frequency_hz INTEGER,
    lead_number           INTEGER
);

CREATE TABLE irn_events (
    event_id   TEXT PRIMARY KEY,
    start_time TEXT NOT NULL,
    end_time   TEXT,
    local_date TEXT NOT NULL,
    window_count INTEGER
);

-- Derived, rebuilt idempotently from the tables above after every sync.
-- Keeping it as a real table rather than a view keeps the dashboard fast and
-- makes the database useful for ad hoc SQL without re-deriving aggregates.
CREATE TABLE daily_summary (
    date                    TEXT PRIMARY KEY,
    steps                   INTEGER,
    distance_mm             REAL,
    floors                  INTEGER,
    calories_active_kcal    REAL,
    calories_basal_kcal     REAL,
    calories_total_kcal     REAL,
    azm_total               INTEGER,
    azm_fat_burn            INTEGER,
    azm_cardio              INTEGER,
    azm_peak                INTEGER,
    active_minutes_light    REAL,
    active_minutes_moderate REAL,
    active_minutes_vigorous REAL,
    sedentary_minutes       REAL,
    resting_hr              INTEGER,
    hr_min                  INTEGER,
    hr_max                  INTEGER,
    hr_avg                  REAL,
    hr_sample_count         INTEGER,
    hrv_rmssd_ms            REAL,
    hrv_deep_sleep_rmssd_ms REAL,
    spo2_avg_percent        REAL,
    breathing_rate          REAL,
    skin_temp_nightly_c     REAL,
    skin_temp_delta_c       REAL,
    sleep_minutes_asleep    REAL,
    sleep_minutes_in_period REAL,
    sleep_minutes_deep      REAL,
    sleep_minutes_light     REAL,
    sleep_minutes_rem       REAL,
    sleep_minutes_awake     REAL,
    sleep_efficiency        REAL,
    sleep_start_time        TEXT,
    sleep_end_time          TEXT,
    weight_grams            REAL,
    body_fat_percent        REAL,
    vo2_max                 REAL,
    workout_count           INTEGER,
    workout_minutes         REAL,
    updated_at              TEXT
);
