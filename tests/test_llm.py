"""Tests for the feature-flagged LLM classifier and its pipeline enrichment.

Uses an injected fake Anthropic client so the full structured-output path is
exercised with no API key and no network."""
from __future__ import annotations

import os
import tempfile

_DB = os.path.join(tempfile.gettempdir(), "watchdog_llm_test.db")
for _ext in ("", "-wal", "-shm"):
    try:
        os.remove(_DB + _ext)
    except OSError:
        pass
os.environ["WATCHDOG_DB_PATH"] = _DB
os.environ["WATCHDOG_GENERATOR"] = "false"
os.environ["WATCHDOG_LLM_ENABLED"] = "false"

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.detectors.llm import LLMClassifier  # noqa: E402
from app.pipeline import DetectionPipeline  # noqa: E402


# --- fake Anthropic client (duck-typed) ----------------------------------

class _Block:
    type = "tool_use"

    def __init__(self, data):
        self.input = data


class _Resp:
    def __init__(self, data):
        self.content = [_Block(data)]


class _Messages:
    def __init__(self, data):
        self._data = data
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _Resp(self._data)


class FakeClient:
    def __init__(self, data):
        self.messages = _Messages(data)


VERDICT = {
    "category": "dependency_outage",
    "probable_root_cause": "Downstream payments dependency is returning 500s.",
    "severity": "high",
    "recommended_action": "Check the payments service health and failover.",
    "confidence": "high",
}


def _anomaly():
    a = {
        "ts": "2026-06-30T00:00:00+00:00", "service": "checkout-api",
        "window_id": None, "score": 0.95, "method": "stats+iforest",
        "features_json": '{"window": {"error_rate": 0.6}, "z_scores": {}}',
        "explanation": "error_rate spike", "severity": "high", "category": None,
    }
    a["id"] = db.insert_anomaly(a)
    return a


def test_disabled_returns_none():
    db.init_db()
    clf = LLMClassifier(get_settings())  # no key, flag off, no injected client
    assert clf.active is False
    assert clf.classify(_anomaly(), []) is None


def test_classify_returns_structured_verdict():
    db.init_db()
    fake = FakeClient(VERDICT)
    clf = LLMClassifier(get_settings(), client=fake)
    assert clf.active is True
    out = clf.classify(_anomaly(), [{"level": "ERROR", "message": "500", "latency_ms": 800}])
    assert out == VERDICT
    # verify strict tool use was wired correctly
    kw = fake.messages.last_kwargs
    assert kw["tool_choice"] == {"type": "tool", "name": "report_root_cause"}
    assert kw["tools"][0]["strict"] is True
    assert kw["model"] == get_settings().llm_model


def test_classify_swallows_errors():
    class Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("network down")
    clf = LLMClassifier(get_settings(), client=Boom())
    assert clf.classify(_anomaly(), []) is None  # never raises


class _NullAlerter:
    def maybe_alert(self, anomaly):
        return None

    def close(self):
        pass


def test_pipeline_enriches_anomaly():
    db.init_db()
    clf = LLMClassifier(get_settings(), client=FakeClient(VERDICT))
    pipe = DetectionPipeline(get_settings(), alerter=_NullAlerter(), classifier=clf)
    anomaly = _anomaly()
    pipe._enrich_llm([anomaly])
    # in-memory dict enriched
    assert anomaly["category"] == "dependency_outage"
    assert anomaly["method"].endswith("+llm")
    assert "recommended" in anomaly["explanation"].lower() or "failover" in anomaly["explanation"].lower()
    assert anomaly.get("suppress_alert") is not True  # real incident → still pages
    # persisted to DB
    row = db.list_anomalies(limit=1)[0]
    assert row["category"] == "dependency_outage"
    assert row["severity"] == "high"


class _RecordingAlerter:
    def __init__(self):
        self.calls = []

    def maybe_alert(self, anomaly):
        self.calls.append(anomaly)

    def close(self):
        pass


def test_noise_verdict_suppresses_alert():
    # When the cost-gated triage layer classifies an anomaly as noise, the
    # pipeline must enrich it but NOT page.
    db.init_db()
    noise = {**VERDICT, "category": "noise", "severity": "low"}
    clf = LLMClassifier(get_settings(), client=FakeClient(noise))
    alerter = _RecordingAlerter()
    pipe = DetectionPipeline(get_settings(), alerter=alerter, classifier=clf)
    anomaly = _anomaly()
    pipe._enrich_llm([anomaly])
    assert anomaly["suppress_alert"] is True
    pipe._dispatch_alerts([anomaly])
    assert alerter.calls == []  # no page for noise
