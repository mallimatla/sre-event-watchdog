"""Tests for the synthetic generator: bucket synthesis honors incidents, and a
seeded baseline + injected incident produces a detected anomaly."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

_DB = os.path.join(tempfile.gettempdir(), "watchdog_gen_test.db")
for _ext in ("", "-wal", "-shm"):
    try:
        os.remove(_DB + _ext)
    except OSError:
        pass
os.environ["WATCHDOG_DB_PATH"] = _DB
os.environ["WATCHDOG_BUCKET_SECONDS"] = "10"
os.environ["WATCHDOG_GENERATOR"] = "false"  # we drive it manually

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.generator import (  # noqa: E402
    DEFAULT_PROFILES,
    SCENARIOS,
    ServiceProfile,
    SyntheticGenerator,
    _Incident,
)
from app.pipeline import DetectionPipeline  # noqa: E402


class _NullAlerter:
    def maybe_alert(self, anomaly):
        return None

    def close(self):
        pass


def _pipeline():
    db.init_db()
    return DetectionPipeline(get_settings(), alerter=_NullAlerter())


def test_healthy_bucket_low_error_rate():
    gen = SyntheticGenerator(_pipeline(), bucket_seconds=10, seed=1)
    p = DEFAULT_PROFILES[0]
    logs = gen._synth_bucket(p, datetime.now(timezone.utc), incident=None)
    assert len(logs) > 0
    errors = sum(1 for lg in logs if lg["level"] == "ERROR")
    assert errors / len(logs) < 0.1  # healthy traffic is mostly clean


def test_incident_raises_error_rate():
    gen = SyntheticGenerator(_pipeline(), bucket_seconds=10, seed=1)
    p = ServiceProfile("svc", rps=200, latency_mean=100, latency_jitter=10, error_rate=0.01)
    incident = _Incident("dependency_outage", remaining=10,
                         params=dict(SCENARIOS["dependency_outage"]))
    logs = gen._synth_bucket(p, datetime.now(timezone.utc), incident)
    err_rate = sum(1 for lg in logs if lg["level"] == "ERROR") / len(logs)
    assert err_rate > 0.3  # outage drives error rate way up


def test_latency_regression_raises_latency():
    gen = SyntheticGenerator(_pipeline(), bucket_seconds=10, seed=2)
    p = ServiceProfile("svc", rps=200, latency_mean=100, latency_jitter=10, error_rate=0.01)
    base = gen._synth_bucket(p, datetime.now(timezone.utc), incident=None)
    incident = _Incident("latency_regression", remaining=10,
                         params=dict(SCENARIOS["latency_regression"]))
    spiked = gen._synth_bucket(p, datetime.now(timezone.utc), incident)
    base_mean = sum(lg["latency_ms"] for lg in base) / len(base)
    spiked_mean = sum(lg["latency_ms"] for lg in spiked) / len(spiked)
    assert spiked_mean > 3 * base_mean


def test_seed_then_inject_detects_anomaly():
    pipe = _pipeline()
    gen = SyntheticGenerator(pipe, bucket_seconds=10, seed=3)
    # Seed enough healthy history to warm both detector layers.
    gen.seed_history(n_buckets=45)
    before = db.count_anomalies()

    # Now feed several incident buckets for one service and finalize them.
    p = next(p for p in gen.profiles if p.name == "checkout-api")
    incident = _Incident("dependency_outage", remaining=10,
                         params=dict(SCENARIOS["dependency_outage"]))
    now = datetime.now(timezone.utc)
    for k in range(3):
        from datetime import timedelta
        gen._feed(gen._synth_bucket(p, now + timedelta(seconds=k * 10), incident))
    # Finalize the last incident bucket with a following healthy bucket.
    from datetime import timedelta
    gen._feed(gen._synth_bucket(p, now + timedelta(seconds=40), incident=None))

    after = db.count_anomalies()
    assert after > before
