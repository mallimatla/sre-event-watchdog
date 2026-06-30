"""Synthetic log generator — makes the whole system self-contained and demoable.

Emits realistic, *noisy* multi-service traffic and periodically injects incident
scenarios so the detectors trip, alerts fire, and the dashboard lights up with no
manual input. Two phases:

1. **Seed** — on startup, backfill ~45 buckets of healthy, backdated history so
   BOTH detector layers (stats warmup + Isolation Forest warmup) are warm within
   seconds. This also gives the dashboard an immediately-populated trend.
2. **Live** — a background thread emits traffic in real time (timestamps = now)
   so windows finalize on the wall clock, and randomly starts incidents.

Incidents can also be triggered on demand via ``POST /api/demo/inject``.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

# --- service traffic profiles (healthy baselines) -------------------------

@dataclass
class ServiceProfile:
    name: str
    rps: float                # logs per bucket (healthy)
    latency_mean: float       # ms
    latency_jitter: float     # ms (std)
    error_rate: float         # healthy fraction


DEFAULT_PROFILES = [
    ServiceProfile("checkout-api", rps=80, latency_mean=120, latency_jitter=25, error_rate=0.01),
    ServiceProfile("auth-service", rps=120, latency_mean=60, latency_jitter=12, error_rate=0.005),
    ServiceProfile("search-api", rps=60, latency_mean=200, latency_jitter=40, error_rate=0.02),
    ServiceProfile("payments", rps=40, latency_mean=150, latency_jitter=30, error_rate=0.015),
]

# --- incident scenarios: multipliers/overrides applied while active --------

SCENARIOS: dict[str, dict[str, float]] = {
    "error_burst":        {"error_rate": 0.45, "latency_mult": 1.3},
    "latency_regression": {"latency_mult": 5.0, "error_rate_add": 0.05},
    "dependency_outage":  {"error_rate": 0.6, "latency_mult": 3.0},
    "traffic_spike":      {"rps_mult": 4.0, "latency_mult": 1.6},
}

_MESSAGES = {
    "INFO": ["request handled", "200 OK", "cache hit", "processed"],
    "WARN": ["slow downstream", "retry scheduled", "cache miss"],
    "ERROR": ["downstream 500", "timeout", "connection reset", "dependency unavailable"],
}


@dataclass
class _Incident:
    scenario: str
    remaining: int            # ticks remaining
    params: dict[str, float] = field(default_factory=dict)


class SyntheticGenerator:
    def __init__(self, pipeline, bucket_seconds: int,
                 profiles: list[ServiceProfile] | None = None,
                 tick_seconds: float = 1.0, seed: int | None = None,
                 incident_prob: float = 0.01):
        self.pipeline = pipeline
        self.bucket_seconds = bucket_seconds
        self.profiles = profiles or DEFAULT_PROFILES
        self.tick_seconds = tick_seconds
        self.incident_prob = incident_prob
        self.rng = np.random.default_rng(seed)
        self._incidents: dict[str, _Incident] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- log synthesis (pure-ish; used by both seed and live) -----------

    def _effective(self, p: ServiceProfile, incident: _Incident | None
                   ) -> tuple[float, float, float]:
        """Return (rps, latency_mean, error_rate) after applying any incident."""
        rps, lat, err = p.rps, p.latency_mean, p.error_rate
        if incident:
            params = incident.params
            rps *= params.get("rps_mult", 1.0)
            lat *= params.get("latency_mult", 1.0)
            if "error_rate" in params:
                err = params["error_rate"]
            err += params.get("error_rate_add", 0.0)
        return rps, lat, min(err, 1.0)

    def _synth_bucket(self, p: ServiceProfile, t0: datetime,
                      incident: _Incident | None,
                      spread_seconds: float | None = None) -> list[dict[str, Any]]:
        """Synthesize one tick/bucket of logs, timestamped across ``spread_seconds``
        starting at ``t0`` (defaults to the full bucket; live ticks pass the tick
        interval so logs don't land in future buckets)."""
        spread = self.bucket_seconds if spread_seconds is None else spread_seconds
        rps, lat_mean, err = self._effective(p, incident)
        n = max(1, int(self.rng.poisson(rps)))
        logs = []
        for i in range(n):
            ts = t0 + timedelta(seconds=(i / n) * spread)
            latency = float(max(1.0, self.rng.normal(lat_mean, p.latency_jitter)))
            roll = self.rng.random()
            if roll < err:
                level = "ERROR"
            elif roll < err + 0.05:
                level = "WARN"
            else:
                level = "INFO"
            msg = _MESSAGES[level][int(self.rng.integers(len(_MESSAGES[level])))]
            logs.append({"service": p.name, "ts": ts.isoformat(),
                         "level": level, "message": msg, "latency_ms": latency})
        return logs

    def _feed(self, logs: list[dict[str, Any]]) -> int:
        anomalies = 0
        for lg in logs:
            anomalies += len(self.pipeline.ingest_log(
                service=lg["service"], ts=lg["ts"], level=lg["level"],
                message=lg["message"], latency_ms=lg["latency_ms"]))
        return anomalies

    # --- seeding ---------------------------------------------------------

    def seed_history(self, n_buckets: int = 45) -> None:
        """Backfill healthy, backdated history so both detectors warm up fast."""
        now = datetime.now(timezone.utc)
        for k in range(n_buckets):
            bucket_start = now - timedelta(
                seconds=(n_buckets - k) * self.bucket_seconds)
            for p in self.profiles:
                self._feed(self._synth_bucket(p, bucket_start, incident=None))

    # --- incidents -------------------------------------------------------

    def inject_incident(self, service: str, scenario: str,
                        duration_ticks: int = 25) -> dict[str, Any]:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario: {scenario}")
        if not any(p.name == service for p in self.profiles):
            raise ValueError(f"unknown service: {service}")
        with self._lock:
            self._incidents[service] = _Incident(
                scenario=scenario, remaining=duration_ticks,
                params=dict(SCENARIOS[scenario]))
        return {"service": service, "scenario": scenario,
                "duration_ticks": duration_ticks}

    def _maybe_start_random_incident(self) -> None:
        for p in self.profiles:
            if p.name in self._incidents:
                continue
            if self.rng.random() < self.incident_prob:
                scenario = list(SCENARIOS)[int(self.rng.integers(len(SCENARIOS)))]
                self._incidents[p.name] = _Incident(
                    scenario=scenario,
                    remaining=int(self.rng.integers(15, 30)),
                    params=dict(SCENARIOS[scenario]))

    # --- live loop -------------------------------------------------------

    def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            self._maybe_start_random_incident()
            # Emit ~one tick's share of each bucket; reuse the bucket synthesizer
            # scaled to the tick interval so live rate matches the profile.
            fraction = self.tick_seconds / self.bucket_seconds
            for p in self.profiles:
                incident = self._incidents.get(p.name)
                scaled = ServiceProfile(
                    p.name, rps=max(1.0, p.rps * fraction), latency_mean=p.latency_mean,
                    latency_jitter=p.latency_jitter, error_rate=p.error_rate)
                # Spread this tick's logs over the tick interval (not the whole
                # bucket) so they don't get timestamped into future buckets and
                # prematurely finalize the current one with a partial count.
                self._feed(self._synth_bucket(
                    scaled, now, incident, spread_seconds=self.tick_seconds))
                if incident:
                    incident.remaining -= 1
                    if incident.remaining <= 0:
                        del self._incidents[p.name]

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001 — generator must never crash the app
                pass
            self._stop.wait(self.tick_seconds)

    def start(self, seed_buckets: int = 45) -> None:
        if seed_buckets:
            self.seed_history(seed_buckets)
        self._thread = threading.Thread(target=self._run, name="synthgen", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def status(self) -> dict[str, Any]:
        # Snapshot _incidents under the lock — the background tick thread mutates
        # it, and iterating concurrently would raise "dictionary changed size".
        with self._lock:
            incidents = {s: i.scenario for s, i in self._incidents.items()}
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "services": [p.name for p in self.profiles],
            "active_incidents": incidents,
        }
