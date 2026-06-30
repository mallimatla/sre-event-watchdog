"""Integration test: ingest synthetic traffic via the API and detect an anomaly."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

# Isolate this test's DB and bucket size BEFORE importing the app/config.
_DB = os.path.join(tempfile.gettempdir(), "watchdog_ingest_test.db")
for _ext in ("", "-wal", "-shm"):
    try:
        os.remove(_DB + _ext)
    except OSError:
        pass
os.environ["WATCHDOG_DB_PATH"] = _DB
os.environ["WATCHDOG_BUCKET_SECONDS"] = "10"
os.environ["WATCHDOG_Z_THRESHOLD"] = "3.0"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()  # pick up the env above for this test's pipeline

from app.main import app  # noqa: E402

BASE = 1_700_000_000


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _batch():
    logs = []
    # 8 normal buckets (stable, low error rate, ~100ms latency).
    for b in range(8):
        for i in range(10):
            logs.append({
                "service": "checkout-api",
                "ts": _iso(BASE + b * 10 + i * 0.5),
                "level": "INFO",
                "message": "ok",
                "latency_ms": 100.0,
            })
    # Spike bucket 8: error burst + latency regression.
    for i in range(10):
        logs.append({
            "service": "checkout-api",
            "ts": _iso(BASE + 80 + i * 0.5),
            "level": "ERROR",
            "message": "downstream 500",
            "latency_ms": 800.0,
        })
    # Trailing log in bucket 9 finalizes the spike bucket so it gets scored.
    logs.append({
        "service": "checkout-api", "ts": _iso(BASE + 90),
        "level": "INFO", "message": "ok", "latency_ms": 100.0,
    })
    return {"logs": logs}


def test_ingest_detects_spike():
    with TestClient(app) as client:
        resp = client.post("/api/logs", json=_batch())
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] == 91
        assert body["anomalies_detected"] >= 1

        anomalies = client.get("/api/anomalies").json()
        assert anomalies["count"] >= 1
        top = anomalies["anomalies"][0]
        assert top["service"] == "checkout-api"
        assert top["method"].startswith("stats")
        assert top["severity"] in {"low", "med", "high"}


def test_single_log_ingest():
    with TestClient(app) as client:
        resp = client.post("/api/logs", json={
            "service": "auth", "level": "INFO", "message": "login ok",
            "latency_ms": 12.3,
        })
        assert resp.status_code == 200
        assert resp.json()["accepted"] == 1
