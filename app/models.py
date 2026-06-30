"""Pydantic request/response models for the API surface."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR"]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LogIn(BaseModel):
    """A single ingested log line."""
    service: str = Field(..., examples=["checkout-api"])
    level: LogLevel = "INFO"
    message: str = Field(..., examples=["request handled"])
    latency_ms: float | None = Field(default=None, ge=0, examples=[42.5])
    ts: str | None = Field(default=None, description="ISO8601; defaults to now (UTC)")

    def normalized_ts(self) -> str:
        return self.ts or _utcnow_iso()


class LogBatchIn(BaseModel):
    logs: list[LogIn]


class IngestResult(BaseModel):
    accepted: int
    anomalies_detected: int = 0


class HealthOut(BaseModel):
    status: str = "ok"
    version: str
    llm_enabled: bool
    llm_active: bool
    logs_ingested: int
    windows: int
    anomalies: int
    alerts: int
    generator_enabled: bool
    active_incidents: dict[str, str] = {}
