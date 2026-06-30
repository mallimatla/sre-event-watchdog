"""Tests for the Alerter: threshold gating, cooldown, and real delivery to the
mock receiver in-process via httpx ASGI transport (no network/server)."""
from __future__ import annotations

import os
import tempfile

_DB = os.path.join(tempfile.gettempdir(), "watchdog_alert_test.db")
for _ext in ("", "-wal", "-shm"):
    try:
        os.remove(_DB + _ext)
    except OSError:
        pass
os.environ["WATCHDOG_DB_PATH"] = _DB
os.environ["WATCHDOG_ALERT_THRESHOLD"] = "0.7"
os.environ["WATCHDOG_ALERT_COOLDOWN_SECONDS"] = "30"
os.environ["WATCHDOG_WEBHOOK_URL"] = "http://mock/webhook"

import httpx  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.alerter import Alerter  # noqa: E402
from app.config import get_settings  # noqa: E402
from mock_receiver.main import app as mock_app, _received  # noqa: E402

get_settings.cache_clear()


def _client() -> TestClient:
    # Starlette's TestClient drives the ASGI mock app synchronously (httpx 0.28
    # ASGITransport is async-only). The Alerter only needs a .post() method.
    return TestClient(mock_app, base_url="http://mock")


def _anomaly(score=0.9, service="checkout-api", aid=None):
    """Persist a real anomaly row (so the alerts FK is satisfied) and return it."""
    a = {
        "ts": "2026-06-30T00:00:00+00:00", "service": service, "window_id": None,
        "score": score, "method": "stats+iforest", "features_json": "{}",
        "explanation": "error_rate spike", "severity": "high", "category": None,
    }
    a["id"] = db.insert_anomaly(a)
    return a


def setup_function(_):
    db.init_db()
    _received.clear()


def test_below_threshold_no_alert():
    alerter = Alerter(get_settings(), client=_client())
    assert alerter.maybe_alert(_anomaly(score=0.5)) is None
    assert len(_received) == 0


def test_delivery_to_mock_receiver():
    alerter = Alerter(get_settings(), client=_client())
    alert = alerter.maybe_alert(_anomaly(score=0.95))
    assert alert is not None
    assert alert["status"] == "sent"
    assert len(_received) == 1
    assert _received[0]["service"] == "checkout-api"
    assert _received[0]["severity"] == "high"


def test_cooldown_suppresses_repeat():
    alerter = Alerter(get_settings(), client=_client())
    first = alerter.maybe_alert(_anomaly(score=0.9, aid=1))
    second = alerter.maybe_alert(_anomaly(score=0.9, aid=2))  # same service, within cooldown
    assert first is not None
    assert second is None
    assert len(_received) == 1


def test_distinct_services_not_suppressed():
    alerter = Alerter(get_settings(), client=_client())
    a = alerter.maybe_alert(_anomaly(score=0.9, service="svc-a", aid=1))
    b = alerter.maybe_alert(_anomaly(score=0.9, service="svc-b", aid=2))
    assert a is not None and b is not None
    assert len(_received) == 2


def test_failed_delivery_recorded():
    # Point at an unroutable URL with a real client → delivery fails, status persisted.
    settings = get_settings()
    alerter = Alerter(settings, client=httpx.Client(timeout=0.2))
    alerter.webhook_url = "http://127.0.0.1:9/webhook"  # nothing listening
    alert = alerter.maybe_alert(_anomaly(score=0.9))
    assert alert is not None
    assert alert["status"] == "failed"


def test_failed_delivery_does_not_suppress_next_alert():
    # Regression: a failed POST must NOT arm the cooldown, or a recovered webhook
    # would still be silently suppressed for the cooldown window.
    alerter = Alerter(get_settings(), client=_client())
    alerter.webhook_url = "http://mock/nope"            # 404 → delivery fails
    first = alerter.maybe_alert(_anomaly(score=0.9))
    assert first["status"] == "failed"
    assert len(_received) == 0

    alerter.webhook_url = "http://mock/webhook"          # webhook recovers
    second = alerter.maybe_alert(_anomaly(score=0.9))    # same service, immediately
    assert second is not None
    assert second["status"] == "sent"
    assert len(_received) == 1                            # NOT suppressed
