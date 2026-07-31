-- ATM cash replenishment planner: SQLite schema.
-- Prototype scale. Constraints are best-effort and measured, not hard-guaranteed.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS denom_recommendations;
DROP TABLE IF EXISTS sim_metrics;
DROP TABLE IF EXISTS sim_trips;
DROP TABLE IF EXISTS validation_results;
DROP TABLE IF EXISTS schedule_loads;
DROP TABLE IF EXISTS schedule;
DROP TABLE IF EXISTS plan_runs;
DROP TABLE IF EXISTS refill_windows;
DROP TABLE IF EXISTS forecast_accuracy;
DROP TABLE IF EXISTS forecasts;
DROP TABLE IF EXISTS atm_state;
DROP TABLE IF EXISTS faults;
DROP TABLE IF EXISTS telemetry;
DROP TABLE IF EXISTS demand_daily;
DROP TABLE IF EXISTS cartridges;
DROP TABLE IF EXISTS atms;

-- ---------------------------------------------------------------- fleet (Req 1)
CREATE TABLE atms (
    atm_id            TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    location_type     TEXT NOT NULL,
    region            TEXT NOT NULL,
    capacity          REAL NOT NULL,
    eligibility_level REAL NOT NULL,   -- 55% of capacity
    low_cash_level    REAL NOT NULL,   -- 25% of capacity
    fault_propensity  REAL NOT NULL,   -- expected faults per day
    base_daily_amount REAL NOT NULL    -- generator parameter, kept for inspection
);

CREATE TABLE cartridges (
    atm_id        TEXT NOT NULL REFERENCES atms(atm_id),
    cartridge_idx INTEGER NOT NULL,
    denomination  INTEGER NOT NULL,
    note_capacity INTEGER NOT NULL,
    PRIMARY KEY (atm_id, cartridge_idx)
);

-- ------------------------------------------------- demand history (Req 3)
CREATE TABLE demand_daily (
    atm_id       TEXT NOT NULL REFERENCES atms(atm_id),
    d            TEXT NOT NULL,
    amount       REAL NOT NULL,
    notes_10     INTEGER NOT NULL DEFAULT 0,
    notes_50     INTEGER NOT NULL DEFAULT 0,
    notes_100    INTEGER NOT NULL DEFAULT 0,
    p0_share     REAL NOT NULL,
    p1_share     REAL NOT NULL,
    p2_share     REAL NOT NULL,
    unmet_amount REAL NOT NULL DEFAULT 0,
    is_holiday   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (atm_id, d)
);
CREATE INDEX idx_demand_d ON demand_daily(d);

-- --------------------------------------------- hourly telemetry (Req 2)
CREATE TABLE telemetry (
    atm_id          TEXT NOT NULL REFERENCES atms(atm_id),
    ts              TEXT NOT NULL,
    total_balance   REAL NOT NULL,
    pct_of_capacity REAL NOT NULL,
    band_state      TEXT NOT NULL,   -- above_eligibility | in_band | below_threshold
    priority_window TEXT NOT NULL,   -- P0 | P1 | P2
    op_state        TEXT NOT NULL,   -- ok | degraded | out_of_service
    cartridge_notes TEXT NOT NULL,   -- JSON list of note counts
    PRIMARY KEY (atm_id, ts)
);
CREATE INDEX idx_tel_ts ON telemetry(ts);

-- ------------------------------------------------------- faults (Req 4)
CREATE TABLE faults (
    fault_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    atm_id              TEXT NOT NULL REFERENCES atms(atm_id),
    category            TEXT NOT NULL,
    disables_dispensing INTEGER NOT NULL,
    start_ts            TEXT NOT NULL,
    detected_ts         TEXT NOT NULL,
    end_ts              TEXT NOT NULL
);
CREATE INDEX idx_faults_atm ON faults(atm_id);

-- current fleet state the planner reads from (Req 2.6)
CREATE TABLE atm_state (
    atm_id           TEXT PRIMARY KEY REFERENCES atms(atm_id),
    as_of            TEXT NOT NULL,
    cartridge_notes  TEXT NOT NULL,   -- JSON list
    total_balance    REAL NOT NULL,
    pct_of_capacity  REAL NOT NULL,
    last_refill_date TEXT
);

-- --------------------------------------------------- forecasts (Req 5)
CREATE TABLE forecasts (
    atm_id      TEXT NOT NULL REFERENCES atms(atm_id),
    as_of       TEXT NOT NULL,
    target_date TEXT NOT NULL,
    horizon     INTEGER NOT NULL,
    q50         REAL NOT NULL,
    q90         REAL NOT NULL,
    q95         REAL NOT NULL,
    p0_share    REAL NOT NULL,
    p1_share    REAL NOT NULL,
    p2_share    REAL NOT NULL,
    PRIMARY KEY (atm_id, as_of, target_date)
);

CREATE TABLE forecast_accuracy (
    model  TEXT NOT NULL,
    metric TEXT NOT NULL,
    value  REAL NOT NULL
);

-- ---------------------------------------------- refill windows (Req 6)
CREATE TABLE refill_windows (
    atm_id               TEXT NOT NULL REFERENCES atms(atm_id),
    as_of                TEXT NOT NULL,
    pct_now              REAL NOT NULL,
    opens_date           TEXT,
    closes_date          TEXT,
    closes_hour          INTEGER,
    depletion_date       TEXT,
    regulatory_deadline  TEXT,
    effective_latest     TEXT,
    driver               TEXT,          -- demand | compliance
    compliance_low_value INTEGER NOT NULL DEFAULT 0,
    slack_days           INTEGER,
    PRIMARY KEY (atm_id, as_of)
);

-- ------------------------------------------------- schedule (Req 7, 8)
CREATE TABLE plan_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    seed        INTEGER,
    config_json TEXT NOT NULL,
    notes       TEXT
);

CREATE TABLE schedule (
    schedule_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL REFERENCES plan_runs(run_id),
    atm_id            TEXT NOT NULL REFERENCES atms(atm_id),
    plan_date         TEXT NOT NULL,
    priority_window   TEXT NOT NULL,
    trip_type         TEXT NOT NULL DEFAULT 'planned',
    compliance_driven INTEGER NOT NULL DEFAULT 0,
    load_total        REAL NOT NULL DEFAULT 0,
    pct_at_visit      REAL,
    urgency           REAL,
    reason            TEXT,
    UNIQUE (run_id, atm_id, plan_date)
);
CREATE INDEX idx_sched_date ON schedule(run_id, plan_date);

CREATE TABLE schedule_loads (
    schedule_id   INTEGER NOT NULL REFERENCES schedule(schedule_id),
    cartridge_idx INTEGER NOT NULL,
    denomination  INTEGER NOT NULL,
    load_notes    INTEGER NOT NULL,
    load_amount   REAL NOT NULL,
    PRIMARY KEY (schedule_id, cartridge_idx)
);

-- ------------------------------------------- validation report (Req 11)
CREATE TABLE validation_results (
    run_id     INTEGER NOT NULL,
    check_name TEXT NOT NULL,
    passed     INTEGER NOT NULL,
    observed   REAL,
    limit_val  REAL,
    detail     TEXT
);

-- -------------------------------- simulation and benchmarks (Req 9, 12, 13)
CREATE TABLE sim_trips (
    policy          TEXT NOT NULL,
    atm_id          TEXT NOT NULL,
    d               TEXT NOT NULL,
    priority_window TEXT,
    trip_type       TEXT NOT NULL,   -- planned | adhoc
    trigger         TEXT,            -- low_cash | fault | schedule
    cash_loaded     REAL NOT NULL DEFAULT 0,
    pct_before      REAL
);
CREATE INDEX idx_simtrips_policy ON sim_trips(policy);

CREATE TABLE sim_metrics (
    policy     TEXT NOT NULL,
    metric     TEXT NOT NULL,
    value      REAL,
    text_value TEXT,
    PRIMARY KEY (policy, metric)
);

-- ------------------------- denomination remix advisory (Req 10.5 - 10.7)
CREATE TABLE denom_recommendations (
    atm_id                 TEXT PRIMARY KEY REFERENCES atms(atm_id),
    current_mix            TEXT NOT NULL,
    recommended_mix        TEXT NOT NULL,
    current_interval_days  REAL,
    projected_interval_days REAL,
    rationale              TEXT
);
