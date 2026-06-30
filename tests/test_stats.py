"""Unit tests for the EWMA + z-score statistical detector."""
from __future__ import annotations

from app.detectors.stats import StatsDetector


def _window(service="svc", count=100, error_rate=0.01,
            latency_mean=100.0, latency_p95=150.0, latency_std=20.0):
    return {
        "service": service, "count": count, "error_rate": error_rate,
        "latency_mean": latency_mean, "latency_p95": latency_p95,
        "latency_std": latency_std,
    }


def test_no_flag_during_warmup():
    det = StatsDetector(z_threshold=3.0, warmup=5)
    # Even a wild window should not flag before warmup completes.
    res = det.update_and_score(_window(error_rate=0.9, latency_mean=900))
    assert res.is_anomaly is False


def test_stable_traffic_not_flagged():
    det = StatsDetector(z_threshold=3.0, warmup=5)
    for _ in range(10):
        res = det.update_and_score(_window())
    assert res.is_anomaly is False
    assert res.score < 0.5


def test_error_spike_flagged():
    det = StatsDetector(z_threshold=3.0, warmup=5)
    for _ in range(8):
        det.update_and_score(_window())
    res = det.update_and_score(_window(error_rate=0.6))
    assert res.is_anomaly is True
    assert res.top_feature == "error_rate"
    assert res.score > 0.5


def test_sustained_anomaly_keeps_flagging():
    # The baseline must freeze during an anomaly so a sustained incident keeps
    # flagging every bucket instead of being learned as the new normal. (Without
    # the freeze, the EWMA absorbs the spike and later buckets stop flagging.)
    det = StatsDetector(z_threshold=3.0, warmup=5)
    for _ in range(15):
        det.update_and_score(_window())
    flags = [det.update_and_score(_window(error_rate=0.6)).is_anomaly
             for _ in range(5)]
    assert all(flags), flags  # every bucket of the ongoing incident flags


def test_missing_latency_does_not_corrupt_baseline():
    # A window with no latency (latency_* = None) must be skipped, not folded in
    # as 0.0 — otherwise a later healthy latency reads as a huge upward spike.
    det = StatsDetector(z_threshold=6.0, warmup=3)
    for _ in range(8):
        det.update_and_score(_window(latency_mean=120.0, latency_p95=160.0,
                                     latency_std=20.0))
    # A window with latency dropped (e.g. logs without latency_ms):
    det.update_and_score({"service": "svc", "count": 100, "error_rate": 0.01,
                          "latency_mean": None, "latency_p95": None,
                          "latency_std": None})
    # Healthy latency returns — must NOT flag a latency anomaly.
    res = det.update_and_score(_window(latency_mean=120.0, latency_p95=160.0,
                                       latency_std=20.0))
    assert res.is_anomaly is False


def test_latency_drop_not_flagged():
    # A latency improvement must NOT be treated as an incident (upward-only).
    det = StatsDetector(z_threshold=3.0, warmup=5)
    for _ in range(8):
        det.update_and_score(_window(latency_mean=200.0))
    res = det.update_and_score(_window(latency_mean=20.0))
    # latency dropping shouldn't flag on the latency feature itself.
    assert res.feature_z["latency_mean"] < 0
