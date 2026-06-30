"""Alerting — webhook delivery with a per-service cooldown.

When an anomaly's score crosses the alert threshold, the Alerter POSTs a JSON
payload to the configured webhook (the local mock receiver by default; swap in a
real Slack/PagerDuty URL and it works unchanged). A per-service cooldown
suppresses repeat alerts so a sustained incident doesn't become an alert storm.
Every delivery attempt (sent or failed) is persisted for the dashboard.

The Alerter accepts an injectable httpx client so tests can drive it against the
mock receiver in-process via ASGI transport — no live server or network needed.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from . import db
from .config import Settings


class Alerter:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.webhook_url = settings.webhook_url
        self.threshold = settings.alert_threshold
        self.cooldown = settings.alert_cooldown_seconds
        self._client = client or httpx.Client(timeout=5.0)
        self._owns_client = client is None
        self._last_alert: dict[str, float] = {}
        self._lock = threading.Lock()

    def _build_payload(self, anomaly: dict[str, Any]) -> dict[str, Any]:
        features = {}
        if anomaly.get("features_json"):
            try:
                features = json.loads(anomaly["features_json"])
            except (ValueError, TypeError):
                features = {}
        return {
            "anomaly_id": anomaly.get("id"),
            "service": anomaly["service"],
            "score": anomaly["score"],
            "severity": anomaly["severity"],
            "method": anomaly["method"],
            "category": anomaly.get("category"),
            "explanation": anomaly.get("explanation"),
            "features": features,
            "dashboard_url": "http://localhost:8000/",
            "detected_at": anomaly.get("ts"),
        }

    def _in_cooldown(self, service: str, now: float) -> bool:
        last = self._last_alert.get(service)
        return last is not None and (now - last) < self.cooldown

    def maybe_alert(self, anomaly: dict[str, Any]) -> dict[str, Any] | None:
        """Deliver an alert for the anomaly if it clears the threshold and the
        service is not in cooldown. Returns the persisted alert row, or None if
        suppressed/below threshold."""
        if anomaly["score"] < self.threshold:
            return None

        service = anomaly["service"]
        now = time.time()
        with self._lock:
            if self._in_cooldown(service, now):
                return None
            # Tentatively reserve the cooldown so concurrent anomalies for the
            # same service don't double-page; rolled back below if delivery fails.
            prev = self._last_alert.get(service)
            self._last_alert[service] = now

        payload = self._build_payload(anomaly)
        ts = datetime.now(timezone.utc).isoformat()
        status = "failed"
        response_json: str | None = None
        try:
            resp = self._client.post(self.webhook_url, json=payload)
            resp.raise_for_status()
            status = "sent"
            try:
                response_json = json.dumps(resp.json())
            except ValueError:
                response_json = json.dumps({"text": resp.text})
        except Exception as exc:  # noqa: BLE001 — record any delivery failure
            response_json = json.dumps({"error": str(exc)})

        if status != "sent":
            # Delivery failed — release the cooldown so the next anomaly can still
            # page once the webhook recovers, instead of being silently suppressed.
            with self._lock:
                if self._last_alert.get(service) == now:
                    if prev is None:
                        self._last_alert.pop(service, None)
                    else:
                        self._last_alert[service] = prev

        alert = {
            "anomaly_id": anomaly.get("id"),
            "ts": ts,
            "status": status,
            "target_url": self.webhook_url,
            "payload_json": json.dumps(payload),
            "response_json": response_json,
        }
        alert["id"] = db.insert_alert(alert)
        return alert

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
