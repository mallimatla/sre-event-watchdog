"""Detection pipeline — orchestrates windowing, the detector layers, hybrid
scoring, and persistence.

Milestone 2 wires Layer 1 (statistical baseline). Layers 2 (Isolation Forest)
and 3 (LLM classifier) plug into :meth:`_detect` in later milestones; the hybrid
scoring contract (``score = max(layer scores)``, LLM enriches) is already in
place so adding them is additive.

Thread-safety: FastAPI runs sync endpoints in a threadpool, so ``ingest_log`` /
``ingest_batch`` mutate shared windowing + baseline state under a lock.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from . import db
from .alerter import Alerter
from .config import Settings
from .detectors.iforest import IForestDetector
from .detectors.llm import LLMClassifier
from .detectors.stats import StatsDetector
from .windowing import WindowAccumulator


def _severity(score: float) -> str:
    if score >= 0.85:
        return "high"
    if score >= 0.7:
        return "med"
    return "low"


class DetectionPipeline:
    def __init__(self, settings: Settings, alerter: Alerter | None = None,
                 classifier: LLMClassifier | None = None):
        self.settings = settings
        self.windower = WindowAccumulator(settings.bucket_seconds)
        self.stats = StatsDetector(z_threshold=settings.z_threshold)
        self.iforest = IForestDetector(warmup=settings.iforest_warmup)
        self.alerter = alerter or Alerter(settings)
        self.classifier = classifier or LLMClassifier(settings)
        self._lock = threading.Lock()

    # --- ingestion -------------------------------------------------------

    def ingest_log(self, service: str, ts: str, level: str, message: str,
                   latency_ms: float | None) -> list[dict[str, Any]]:
        """Persist a log, advance windowing, and run detection on any window
        that just finalized. Returns the list of anomalies detected (if any)."""
        db.insert_log(ts, service, level, message, latency_ms,
                      raw={"service": service, "level": level,
                           "message": message, "latency_ms": latency_ms})
        anomalies: list[dict[str, Any]] = []
        with self._lock:
            finalized = self.windower.add(service, ts, level, latency_ms)
            for window in finalized:
                anomalies.extend(self._detect(window))
        # Enrich + alert outside the detection lock — LLM and webhook I/O must
        # not block ingestion. LLM is cost-gated: only flagged anomalies reach it.
        self._enrich_llm(anomalies)
        self._dispatch_alerts(anomalies)
        return anomalies

    def ingest_batch(self, logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        anomalies: list[dict[str, Any]] = []
        for lg in logs:
            anomalies.extend(self.ingest_log(
                service=lg["service"], ts=lg["ts"], level=lg["level"],
                message=lg["message"], latency_ms=lg.get("latency_ms"),
            ))
        return anomalies

    def flush(self) -> list[dict[str, Any]]:
        anomalies: list[dict[str, Any]] = []
        with self._lock:
            for window in self.windower.flush():
                anomalies.extend(self._detect(window))
        self._enrich_llm(anomalies)
        self._dispatch_alerts(anomalies)
        return anomalies

    def _enrich_llm(self, anomalies: list[dict[str, Any]]) -> None:
        """Cost-gated LLM enrichment: adds root-cause category + recommended
        action to already-flagged anomalies. No-op when the layer is disabled."""
        if not self.classifier.active:
            return
        for anomaly in anomalies:
            verdict = self.classifier.classify(
                anomaly, db.recent_logs(anomaly["service"], limit=15))
            if not verdict:
                continue
            category = verdict.get("category")
            severity = verdict.get("severity", anomaly["severity"])
            explanation = (
                f"{verdict.get('probable_root_cause', '')} "
                f"→ {verdict.get('recommended_action', '')} "
                f"(LLM confidence: {verdict.get('confidence', '?')})"
            ).strip()
            anomaly["category"] = category
            anomaly["severity"] = severity
            anomaly["explanation"] = explanation
            anomaly["method"] = anomaly["method"] + "+llm"
            # If the cost-gated triage layer judged this benign, don't page. The
            # anomaly is still recorded (and shown on the dashboard) as noise.
            if category == "noise":
                anomaly["suppress_alert"] = True
            db.update_anomaly_enrichment(
                anomaly["id"], category, severity, explanation)

    def _dispatch_alerts(self, anomalies: list[dict[str, Any]]) -> None:
        for anomaly in anomalies:
            if anomaly.get("suppress_alert"):
                continue
            self.alerter.maybe_alert(anomaly)

    # --- detection -------------------------------------------------------

    def _detect(self, window: dict[str, Any]) -> list[dict[str, Any]]:
        window_id = db.insert_window(window)

        stat = self.stats.update_and_score(window)
        ifr = self.iforest.update_and_score(window)

        # Hybrid scoring: a window is anomalous if EITHER layer flags it; the
        # final score is the strongest layer's. Each layer is recorded so the
        # dashboard can show which detector caught the incident.
        methods: list[str] = []
        if stat.is_anomaly:
            methods.append("stats")
        if ifr.active and ifr.is_anomaly:
            methods.append("iforest")

        if not methods:
            return []

        ifr_score = ifr.score if ifr.active else 0.0
        # The final score combines only the layers that actually flagged — an
        # active-but-non-flagging Isolation Forest must not inflate the severity
        # of a stats-only anomaly (or vice versa) and trip the alert threshold.
        contributing = [s for s, on in (
            (stat.score, stat.is_anomaly),
            (ifr.score, ifr.active and ifr.is_anomaly),
        ) if on]
        score = max(contributing)
        method = "+".join(methods) if len(methods) > 1 else methods[0]
        explanation = " | ".join(
            part for part, on in (
                (stat.explain(), stat.is_anomaly),
                (ifr.explain(), ifr.active and ifr.is_anomaly),
            ) if on
        )

        anomaly = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "service": window["service"],
            "window_id": window_id,
            "score": round(score, 4),
            "method": method,
            "features_json": json.dumps({
                "window": {k: window[k] for k in (
                    "count", "error_rate", "latency_mean", "latency_p95", "latency_std")},
                "z_scores": {k: round(v, 3) for k, v in stat.feature_z.items()},
                "layer_scores": {
                    "stats": round(stat.score, 3),
                    "iforest": round(ifr_score, 3),
                },
            }),
            "explanation": explanation,
            "severity": _severity(score),
            "category": None,
        }
        anomaly_id = db.insert_anomaly(anomaly)
        anomaly["id"] = anomaly_id
        return [anomaly]
