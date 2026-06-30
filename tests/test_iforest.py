"""Unit tests for the Isolation Forest (Layer 2) detector."""
from __future__ import annotations

import numpy as np

from app.detectors.iforest import IForestDetector


def _window(service="svc", count=100.0, error_rate=0.01,
            latency_mean=100.0, latency_p95=150.0, latency_std=20.0):
    return {
        "service": service, "count": count, "error_rate": error_rate,
        "latency_mean": latency_mean, "latency_p95": latency_p95,
        "latency_std": latency_std,
    }


def _normal_window(rng):
    """A realistic 'healthy' window: every feature has natural variance, so the
    forest can split on each dimension (unlike perfectly-constant data)."""
    return _window(
        count=float(rng.normal(100, 8)),
        error_rate=float(abs(rng.normal(0.01, 0.005))),
        latency_mean=float(rng.normal(100, 6)),
        latency_p95=float(rng.normal(150, 10)),
        latency_std=float(rng.normal(20, 3)),
    )


def test_inactive_during_warmup():
    det = IForestDetector(warmup=30)
    res = det.update_and_score(_window())
    assert res.active is False
    assert res.is_anomaly is False
    assert res.score == 0.0


def test_active_after_warmup_normal_not_flagged():
    det = IForestDetector(warmup=30)
    rng = np.random.default_rng(7)
    res = None
    for _ in range(40):
        res = det.update_and_score(_normal_window(rng))
    assert res.active is True
    assert res.is_anomaly is False


def test_multivariate_outlier_flagged():
    det = IForestDetector(warmup=30, threshold=0.85)
    rng = np.random.default_rng(7)
    for _ in range(40):
        det.update_and_score(_normal_window(rng))
    # A joint spike across error_rate, latency, and volume.
    res = det.update_and_score(_window(
        count=400, error_rate=0.7, latency_mean=900,
        latency_p95=1200, latency_std=300))
    assert res.active is True
    assert res.is_anomaly is True
    assert res.score >= 0.85
