"""Milestone 1 smoke test: app boots, DB initializes, health endpoint responds."""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("WATCHDOG_DB_PATH", os.path.join(tempfile.gettempdir(), "watchdog_test.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_health_ok():
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert "llm_enabled" in body


def test_dashboard_renders():
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "SRE Event Watchdog" in resp.text


def test_docs_reachable():
    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 200
