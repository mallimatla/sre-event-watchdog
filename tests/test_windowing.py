"""Unit tests for time-bucket windowing and feature extraction."""
from __future__ import annotations

from datetime import datetime, timezone

from app.windowing import WindowAccumulator

BASE = 1_700_000_000  # fixed epoch for deterministic buckets


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def test_bucket_rollover_finalizes_previous():
    acc = WindowAccumulator(bucket_seconds=10)
    # Two logs in bucket 0, then one log in bucket 1 triggers finalize of bucket 0.
    assert acc.add("svc", _iso(BASE + 1), "INFO", 100.0) == []
    assert acc.add("svc", _iso(BASE + 2), "ERROR", 200.0) == []
    finalized = acc.add("svc", _iso(BASE + 11), "INFO", 100.0)
    assert len(finalized) == 1
    w = finalized[0]
    assert w["service"] == "svc"
    assert w["count"] == 2
    assert w["error_count"] == 1
    assert w["error_rate"] == 0.5
    assert w["latency_mean"] == 150.0


def test_features_latency_stats():
    acc = WindowAccumulator(bucket_seconds=10)
    for i, lat in enumerate([10, 20, 30, 40, 100]):
        acc.add("svc", _iso(BASE + i * 0.1), "INFO", float(lat))
    w = acc.flush()[0]
    assert w["count"] == 5
    assert w["error_rate"] == 0.0
    assert w["latency_mean"] == 40.0
    assert w["latency_p95"] >= 40.0  # p95 weighted toward the 100 outlier


def test_per_service_isolation():
    acc = WindowAccumulator(bucket_seconds=10)
    acc.add("a", _iso(BASE + 1), "INFO", 10.0)
    acc.add("b", _iso(BASE + 1), "ERROR", 20.0)
    windows = {w["service"]: w for w in acc.flush()}
    assert windows["a"]["error_rate"] == 0.0
    assert windows["b"]["error_rate"] == 1.0
