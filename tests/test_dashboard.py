"""Tests for the dashboard timeseries endpoint and page render."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

_DB = os.path.join(tempfile.gettempdir(), "watchdog_dash_test.db")
for _ext in ("", "-wal", "-shm"):
    try:
        os.remove(_DB + _ext)
    except OSError:
        pass
os.environ["WATCHDOG_DB_PATH"] = _DB
os.environ["WATCHDOG_BUCKET_SECONDS"] = "10"
os.environ["WATCHDOG_GENERATOR"] = "false"  # keep the test fast and deterministic

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.main import app  # noqa: E402

BASE = 1_700_000_000


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _ingest_some(client):
    logs = []
    for b in range(6):
        for i in range(8):
            logs.append({"service": "svc-a", "ts": _iso(BASE + b * 10 + i),
                         "level": "INFO", "message": "ok", "latency_ms": 100.0})
            logs.append({"service": "svc-b", "ts": _iso(BASE + b * 10 + i),
                         "level": "INFO", "message": "ok", "latency_ms": 50.0})
    logs.append({"service": "svc-a", "ts": _iso(BASE + 70), "level": "INFO",
                 "message": "ok", "latency_ms": 100.0})
    client.post("/api/logs", json={"logs": logs})


def test_timeseries_shape():
    with TestClient(app) as client:
        _ingest_some(client)
        ts = client.get("/api/stats/timeseries").json()
        assert set(ts) >= {"labels", "services", "series", "summary",
                           "anomalies", "alert_threshold"}
        assert "svc-a" in ts["services"] and "svc-b" in ts["services"]
        # series arrays are aligned to the labels axis
        n = len(ts["labels"])
        for s in ts["services"]:
            assert len(ts["series"][s]["count"]) == n
            assert len(ts["series"][s]["error_rate"]) == n
            assert len(ts["series"][s]["latency_p95"]) == n
        assert ts["alert_threshold"] == get_settings().alert_threshold


def test_dashboard_page_renders():
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "SRE Event Watchdog" in r.text
        assert "c-anomaly" in r.text  # the anomaly chart canvas is present
