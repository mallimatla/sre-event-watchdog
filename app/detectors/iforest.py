"""Layer 2 — Isolation Forest (multivariate ML).

The statistical layer scores each feature independently. Real incidents are
often *contextual*: normal volume but a skewed latency distribution, or a modest
error-rate bump that only matters alongside rising latency. Isolation Forest
scores the full feature vector jointly and catches these correlations.

Design for a streaming service:
- A bounded rolling buffer of recent windows per service (constant memory).
- A warmup gate: no scoring until enough history exists (cold-start safe).
- Periodic refit (not every window) to bound CPU.
- Raw scores normalized to [0, 1] against the training distribution so the
  output composes cleanly with the statistical layer's score.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest

from ..windowing import FEATURES

_EPS = 1e-9


def _vector(window: dict) -> list[float]:
    out = []
    for f in FEATURES:
        v = window.get(f)
        out.append(float(v) if v is not None else 0.0)
    return out


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


@dataclass
class IForestResult:
    active: bool          # False during warmup (not enough history)
    is_anomaly: bool
    score: float          # normalized [0, 1]

    def explain(self) -> str:
        if not self.active:
            return "isolation-forest warming up"
        return f"isolation-forest multivariate score {self.score:.2f}"


@dataclass
class _ServiceModel:
    buffer: deque
    model: IsolationForest | None = None
    raw_mean: float = 0.0
    raw_std: float = 1.0
    since_fit: int = 0


class IForestDetector:
    def __init__(self, warmup: int = 30, refit_every: int = 10,
                 buffer_size: int = 200, threshold: float = 0.9,
                 random_state: int = 42):
        self.warmup = warmup
        self.refit_every = refit_every
        self.buffer_size = buffer_size
        self.threshold = threshold
        self.random_state = random_state
        self._models: dict[str, _ServiceModel] = {}

    def _fit(self, sm: _ServiceModel) -> None:
        X = np.array(sm.buffer, dtype=float)
        model = IsolationForest(
            n_estimators=100,
            contamination="auto",
            random_state=self.random_state,
        )
        model.fit(X)
        # Calibrate normalization against the training distribution.
        raw = -model.decision_function(X)        # higher = more anomalous
        sm.model = model
        sm.raw_mean = float(np.mean(raw))
        sm.raw_std = float(np.std(raw)) + _EPS
        sm.since_fit = 0

    def update_and_score(self, window: dict) -> IForestResult:
        service = window["service"]
        sm = self._models.get(service)
        if sm is None:
            sm = _ServiceModel(buffer=deque(maxlen=self.buffer_size))
            self._models[service] = sm

        x = _vector(window)

        if len(sm.buffer) < self.warmup:
            sm.buffer.append(x)
            return IForestResult(active=False, is_anomaly=False, score=0.0)

        if sm.model is None or sm.since_fit >= self.refit_every:
            self._fit(sm)
        sm.since_fit += 1

        raw = float(-sm.model.decision_function([x])[0])
        z = (raw - sm.raw_mean) / sm.raw_std
        score = _sigmoid(z)
        result = IForestResult(
            active=True,
            is_anomaly=score >= self.threshold,
            score=score,
        )
        # Keep confirmed anomalies out of the rolling training buffer so a
        # sustained incident isn't progressively absorbed into the "normal"
        # model (which would make Layer 2 stop flagging mid-outage).
        if not result.is_anomaly:
            sm.buffer.append(x)
        return result
