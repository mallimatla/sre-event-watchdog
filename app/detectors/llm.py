"""Layer 3 — LLM root-cause classifier (the GenAI showcase, feature-flagged).

Turns a numeric anomaly into an actionable, structured root-cause verdict using
Claude. Three deliberate engineering choices make this production-shaped:

1. **Cost-gated** — this layer is the expensive one, so the pipeline only calls
   it on windows the cheap detectors (stats / Isolation Forest) already flagged.
2. **Structured output** — uses strict tool use (a forced ``report_root_cause``
   tool with an enum-constrained schema), so Claude's verdict is machine-usable,
   not free text to parse.
3. **Graceful fallback** — runs only when ``WATCHDOG_LLM_ENABLED`` is set AND an
   API key is present. Otherwise ``classify`` returns ``None`` and the anomaly is
   recorded with its stats/ML explanation. The app behaves identically minus the
   enrichment, so it always runs with no key.

The Anthropic client is injectable so tests exercise the full path without a key
or network.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import Settings

CATEGORIES = [
    "deployment_regression",
    "dependency_outage",
    "traffic_spike",
    "resource_exhaustion",
    "noise",
]

_TOOL = {
    "name": "report_root_cause",
    "description": (
        "Report the probable root cause of an anomaly detected in service logs, "
        "as a structured triage verdict for an on-call SRE."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": CATEGORIES,
                         "description": "Best-fit incident category."},
            "probable_root_cause": {
                "type": "string",
                "description": "One or two sentences on the most likely cause."},
            "severity": {"type": "string", "enum": ["low", "med", "high"],
                         "description": "Operational severity."},
            "recommended_action": {
                "type": "string",
                "description": "The single most useful next step for the on-call."},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"],
                           "description": "Confidence in this assessment."},
        },
        "required": ["category", "probable_root_cause", "severity",
                     "recommended_action", "confidence"],
        "additionalProperties": False,
    },
}

_SYSTEM = (
    "You are an SRE incident triage assistant. Given an anomalous metric window "
    "and a sample of raw log lines from one service, identify the most probable "
    "root cause. Be concise and practical. Always answer by calling the "
    "report_root_cause tool."
)


class LLMClassifier:
    def __init__(self, settings: Settings, client: Any | None = None):
        self.settings = settings
        self.model = settings.llm_model
        self._client = client
        self._client_injected = client is not None

    @property
    def active(self) -> bool:
        # An injected client (tests) is always active; otherwise require the
        # feature flag + a real API key.
        return self._client_injected or self.settings.llm_active

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic  # lazy — app runs without the SDK on the disabled path
            self._client = anthropic.Anthropic(
                api_key=self.settings.anthropic_api_key)
        return self._client

    def _prompt(self, anomaly: dict[str, Any], sample_logs: list[dict]) -> str:
        features = {}
        if anomaly.get("features_json"):
            try:
                features = json.loads(anomaly["features_json"])
            except (ValueError, TypeError):
                features = {}
        lines = "\n".join(
            f"  [{lg['level']}] {lg.get('latency_ms', '?')}ms {lg['message']}"
            for lg in sample_logs[:15]
        ) or "  (no sample lines available)"
        return (
            f"Service: {anomaly['service']}\n"
            f"Detected by: {anomaly['method']} (score {anomaly['score']})\n"
            f"Statistical explanation: {anomaly.get('explanation', '')}\n"
            f"Window features / z-scores: {json.dumps(features.get('window', {}))} "
            f"{json.dumps(features.get('z_scores', {}))}\n"
            f"Sample log lines:\n{lines}\n"
        )

    def classify(self, anomaly: dict[str, Any],
                 sample_logs: list[dict]) -> dict[str, Any] | None:
        """Return a structured root-cause verdict, or None if the layer is
        disabled or the call fails (never raises — enrichment is best-effort)."""
        if not self.active:
            return None
        try:
            client = self._get_client()
            resp = client.messages.create(
                model=self.model,
                max_tokens=512,
                system=_SYSTEM,
                tools=[_TOOL],
                tool_choice={"type": "tool", "name": "report_root_cause"},
                messages=[{"role": "user",
                           "content": self._prompt(anomaly, sample_logs)}],
            )
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    return dict(block.input)
        except Exception:  # noqa: BLE001 — enrichment must never break detection
            return None
        return None
