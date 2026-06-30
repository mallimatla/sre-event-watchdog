"""Layer 1 — online statistical baseline (EWMA mean/variance + z-score).

Per service, per feature, we maintain an exponentially-weighted moving average
and variance (West's incremental EWMA variance). Each new window is scored by
its z-score against that baseline. This layer is cheap, interpretable, and
cold-start friendly — it produces a useful signal from the very first windows.

Direction matters for SRE semantics: a *drop* in latency or error_rate is not an
incident, so those features are scored on upward deviation only. Volume (count)
is scored on absolute deviation (both a spike and a traffic blackout matter).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..windowing import FEATURES

# Features where only an upward deviation is "concerning".
_UPWARD_ONLY = {"error_rate", "latency_mean", "latency_p95", "latency_std"}
_EPS = 1e-9


@dataclass
class _Baseline:
    mean: float = 0.0
    var: float = 1.0
    n: int = 0


@dataclass
class StatResult:
    is_anomaly: bool
    score: float                       # normalized [0, 1]
    feature_z: dict[str, float] = field(default_factory=dict)
    top_feature: str | None = None
    top_z: float = 0.0

    def explain(self) -> str:
        if self.top_feature is None:
            return "within statistical baseline"
        return (f"{self.top_feature} deviated {self.top_z:.1f}σ from EWMA baseline "
                f"(z-scores: " +
                ", ".join(f"{k}={v:+.1f}" for k, v in self.feature_z.items()) + ")")


class StatsDetector:
    """Online EWMA z-score detector with per-service baselines."""

    def __init__(self, z_threshold: float = 3.0, alpha: float = 0.4,
                 warmup: int = 5):
        self.z_threshold = z_threshold
        self.alpha = alpha
        self.warmup = warmup
        self._baselines: dict[tuple[str, str], _Baseline] = {}

    def update_and_score(self, window: dict) -> StatResult:
        service = window["service"]
        feature_z: dict[str, float] = {}
        warm = True
        present: list[tuple[str, float, _Baseline]] = []

        # Pass 1 — score each *present* feature against the current baseline.
        # A missing feature (e.g. a window with no latency) is skipped entirely:
        # folding a placeholder 0.0 into the EWMA would corrupt the baseline and
        # later flag healthy traffic as a huge upward deviation.
        for feature in FEATURES:
            v = window.get(feature)
            if v is None:
                continue
            x = float(v)
            key = (service, feature)
            b = self._baselines.get(key)
            if b is None:
                b = _Baseline(mean=x, var=1.0, n=0)
                self._baselines[key] = b
            if b.n < self.warmup:
                warm = False
            std = (b.var ** 0.5) + _EPS
            feature_z[feature] = (x - b.mean) / std
            present.append((feature, x, b))

        # Reduce per-feature z to a single concern score.
        concern = {f: (z if f in _UPWARD_ONLY else abs(z))
                   for f, z in feature_z.items()}
        top_feature = max(concern, key=concern.get) if concern else None
        top_z = concern[top_feature] if top_feature is not None else 0.0

        is_anomaly = warm and top_z > self.z_threshold
        # Map z to [0,1]: z==threshold -> 0.5, z==2*threshold -> 1.0.
        score = max(0.0, min(1.0, top_z / (2 * self.z_threshold)))

        # Pass 2 — update baselines, but FREEZE during a confirmed anomaly so a
        # sustained incident keeps flagging every bucket instead of being learned
        # as the new normal within a few windows. (Warmup windows never flag, so
        # they always update and establish the baseline. Trade-off: a permanent
        # legitimate level shift will keep alerting until it ends — acceptable for
        # an SRE watchdog, where a sustained 2x is worth surfacing.)
        if not is_anomaly:
            for feature, x, b in present:
                diff = x - b.mean
                incr = self.alpha * diff
                b.mean += incr
                b.var = (1 - self.alpha) * (b.var + diff * incr)
                b.n += 1

        return StatResult(
            is_anomaly=is_anomaly,
            score=score,
            feature_z=feature_z,
            top_feature=top_feature if is_anomaly else None,
            top_z=top_z,
        )
