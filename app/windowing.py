"""Time-bucket windowing and multi-feature extraction.

Logs are aggregated per service into fixed-width time buckets. When a service's
traffic rolls into a newer bucket, the previous bucket is *finalized* — its
feature vector is computed and handed to the detection pipeline. This is an
online, streaming design: no batch jobs, constant memory per service.

Feature vector per window:
    [count, error_rate, latency_mean, latency_p95, latency_std]
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np

FEATURES = ["count", "error_rate", "latency_mean", "latency_p95", "latency_std"]
_ERROR_LEVELS = {"ERROR"}


def _parse_epoch(ts: str) -> float:
    """Parse ISO8601 (with optional trailing Z) to a UTC epoch float."""
    s = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


class _OpenBucket:
    __slots__ = ("service", "key", "start", "count", "error_count", "latencies")

    def __init__(self, service: str, key: int, start: float):
        self.service = service
        self.key = key
        self.start = start
        self.count = 0
        self.error_count = 0
        self.latencies: list[float] = []

    def add(self, level: str, latency_ms: float | None) -> None:
        self.count += 1
        if level in _ERROR_LEVELS:
            self.error_count += 1
        if latency_ms is not None:
            self.latencies.append(latency_ms)

    def finalize(self, bucket_seconds: int) -> dict[str, Any]:
        lat = np.array(self.latencies, dtype=float) if self.latencies else None
        return {
            "service": self.service,
            "bucket_start": _iso(self.start),
            "bucket_end": _iso(self.start + bucket_seconds),
            "count": self.count,
            "error_count": self.error_count,
            "error_rate": (self.error_count / self.count) if self.count else 0.0,
            "latency_mean": float(np.mean(lat)) if lat is not None else None,
            "latency_p95": float(np.percentile(lat, 95)) if lat is not None else None,
            "latency_std": float(np.std(lat)) if lat is not None else None,
        }


class WindowAccumulator:
    """Streaming per-service bucket accumulator.

    Call :meth:`add` for each log; it returns a list of finalized window dicts
    (usually empty, or one window when a service rolls into a new bucket).
    :meth:`flush` finalizes all currently-open buckets (e.g. at shutdown or in
    tests).
    """

    def __init__(self, bucket_seconds: int):
        self.bucket_seconds = bucket_seconds
        self._open: dict[str, _OpenBucket] = {}

    def _key(self, epoch: float) -> int:
        return int(epoch // self.bucket_seconds)

    def add(self, service: str, ts: str, level: str,
            latency_ms: float | None) -> list[dict[str, Any]]:
        epoch = _parse_epoch(ts)
        key = self._key(epoch)
        finalized: list[dict[str, Any]] = []

        current = self._open.get(service)
        if current is None:
            self._open[service] = _OpenBucket(service, key, key * self.bucket_seconds)
            current = self._open[service]

        if key > current.key:
            # Rolled into a newer bucket: finalize the old one, open a new one.
            finalized.append(current.finalize(self.bucket_seconds))
            self._open[service] = _OpenBucket(service, key, key * self.bucket_seconds)
            current = self._open[service]

        # Late logs (key < current.key) are folded into the current bucket.
        current.add(level, latency_ms)
        return finalized

    def flush(self) -> list[dict[str, Any]]:
        finalized = [b.finalize(self.bucket_seconds) for b in self._open.values()]
        self._open.clear()
        return finalized
