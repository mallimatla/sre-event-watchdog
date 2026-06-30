"""Entrypoint: ``python -m app`` starts the API + dashboard via uvicorn.

Host/port are configurable through APP_HOST / APP_PORT env vars (defaults
0.0.0.0:8000). The synthetic generator and detection wiring attach to the
FastAPI app in later milestones.
"""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
