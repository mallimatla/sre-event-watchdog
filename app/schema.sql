-- SRE Event Watchdog schema (SQLite). Created idempotently on startup.

CREATE TABLE IF NOT EXISTS logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,            -- ISO8601 UTC
    service     TEXT    NOT NULL,
    level       TEXT    NOT NULL,            -- DEBUG/INFO/WARN/ERROR
    message     TEXT    NOT NULL,
    latency_ms  REAL,
    raw_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_service_ts ON logs (service, ts);

CREATE TABLE IF NOT EXISTS windows (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    service       TEXT    NOT NULL,
    bucket_start  TEXT    NOT NULL,
    bucket_end    TEXT    NOT NULL,
    count         INTEGER NOT NULL,
    error_count   INTEGER NOT NULL,
    error_rate    REAL    NOT NULL,
    latency_mean  REAL,
    latency_p95   REAL,
    latency_std   REAL
);
CREATE INDEX IF NOT EXISTS idx_windows_service_start ON windows (service, bucket_start);

CREATE TABLE IF NOT EXISTS anomalies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    service       TEXT    NOT NULL,
    window_id     INTEGER,
    score         REAL    NOT NULL,
    method        TEXT    NOT NULL,          -- stats|iforest|llm|hybrid
    features_json TEXT,
    explanation   TEXT,
    severity      TEXT    NOT NULL,          -- low|med|high
    category      TEXT,                      -- LLM category (nullable)
    FOREIGN KEY (window_id) REFERENCES windows (id)
);
CREATE INDEX IF NOT EXISTS idx_anomalies_ts ON anomalies (ts);

CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    anomaly_id    INTEGER NOT NULL,
    ts            TEXT    NOT NULL,
    status        TEXT    NOT NULL,          -- sent|failed|mock_ack
    target_url    TEXT,
    payload_json  TEXT,
    response_json TEXT,
    FOREIGN KEY (anomaly_id) REFERENCES anomalies (id)
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts (ts);
