"""Mock webhook receiver.

A tiny standalone FastAPI app that stands in for an external alerting system.
It accepts alert POSTs, stores them in memory, and exposes them for inspection
in the demo and integration tests. Swap WATCHDOG_WEBHOOK_URL for a real Slack /
PagerDuty endpoint and the watchdog talks to it unchanged.

Run: ``uvicorn mock_receiver.main:app --port 8001``
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

app = FastAPI(title="Mock Alert Receiver", version="0.1.0")

_received: list[dict[str, Any]] = []


@app.post("/webhook")
async def webhook(request: Request) -> dict[str, Any]:
    payload = await request.json()
    _received.append(payload)
    return {"status": "mock_ack", "received_count": len(_received)}


@app.get("/received")
def received() -> dict[str, Any]:
    return {"count": len(_received), "alerts": _received}


@app.delete("/received")
def clear() -> dict[str, str]:
    _received.clear()
    return {"status": "cleared"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
