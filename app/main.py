"""FastAPI application: API surface + server-rendered dashboard.

Milestone 1 wires the app skeleton: lifespan that initializes the DB, a health
endpoint, and a dashboard placeholder. Detection, alerting, generator, and the
LLM layer are added in subsequent milestones (see SPEC.md build plan).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from . import __version__, db
from .config import get_settings
from .generator import SyntheticGenerator
from .models import HealthOut, IngestResult, LogBatchIn, LogIn
from .pipeline import DetectionPipeline

_BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

# Single shared pipeline instance (holds windowing + baseline state).
pipeline: DetectionPipeline | None = None
generator: SyntheticGenerator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline, generator
    settings = get_settings()
    db.init_db()
    pipeline = DetectionPipeline(settings)
    if settings.generator:
        generator = SyntheticGenerator(pipeline, settings.bucket_seconds)
        generator.start()
    yield
    # Finalize any open windows on shutdown so no data is silently dropped.
    if generator is not None:
        generator.stop()
    if pipeline is not None:
        pipeline.flush()
        pipeline.alerter.close()


app = FastAPI(
    title="SRE Event Watchdog",
    description=(
        "Intelligent Observability & Event Watchdog — hybrid statistical + ML + "
        "LLM anomaly detection over application logs. Fully local, API-first."
    ),
    version=__version__,
    lifespan=lifespan,
)

_static_dir = _BASE / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/api/health", response_model=HealthOut, tags=["ops"])
def health() -> HealthOut:
    settings = get_settings()
    return HealthOut(
        version=__version__,
        llm_enabled=settings.llm_enabled,
        llm_active=settings.llm_active,
        logs_ingested=db.count_logs(),
        windows=db.count_windows(),
        anomalies=db.count_anomalies(),
        alerts=db.count_alerts(),
        generator_enabled=settings.generator,
        active_incidents=(generator.status()["active_incidents"]
                          if generator is not None else {}),
    )


@app.post("/api/logs", response_model=IngestResult, tags=["ingest"])
def ingest_logs(payload: LogIn | LogBatchIn) -> IngestResult:
    """Ingest a single log or a batch. Each finalized window is scored by the
    detection pipeline; any anomalies are persisted and counted in the result."""
    assert pipeline is not None
    if isinstance(payload, LogBatchIn):
        logs = [
            {"service": lg.service, "ts": lg.normalized_ts(), "level": lg.level,
             "message": lg.message, "latency_ms": lg.latency_ms}
            for lg in payload.logs
        ]
        anomalies = pipeline.ingest_batch(logs)
        return IngestResult(accepted=len(logs), anomalies_detected=len(anomalies))

    anomalies = pipeline.ingest_log(
        service=payload.service, ts=payload.normalized_ts(), level=payload.level,
        message=payload.message, latency_ms=payload.latency_ms,
    )
    return IngestResult(accepted=1, anomalies_detected=len(anomalies))


@app.get("/api/anomalies", tags=["detection"])
def get_anomalies(
    limit: int = Query(50, ge=1, le=500),
    service: str | None = None,
    severity: str | None = None,
) -> dict:
    rows = db.list_anomalies(limit=limit, service=service, severity=severity)
    return {"count": len(rows), "anomalies": rows}


@app.get("/api/alerts", tags=["alerting"])
def get_alerts(limit: int = Query(50, ge=1, le=500)) -> dict:
    rows = db.list_alerts(limit=limit)
    return {"count": len(rows), "alerts": rows}


@app.post("/api/demo/inject", tags=["demo"])
def inject_incident(
    service: str = Query(..., description="Target service name"),
    scenario: str = Query("dependency_outage",
                          description="error_burst | latency_regression | "
                                      "dependency_outage | traffic_spike"),
    duration_ticks: int = Query(25, ge=1, le=300),
) -> dict:
    """Trigger a synthetic incident on demand for a live demo. Requires the
    generator to be enabled (WATCHDOG_GENERATOR=true)."""
    if generator is None:
        return {"error": "generator disabled; set WATCHDOG_GENERATOR=true"}
    try:
        return generator.inject_incident(service, scenario, duration_ticks)
    except ValueError as exc:
        return {"error": str(exc)}


@app.get("/api/stats/timeseries", tags=["dashboard"])
def timeseries(buckets: int = Query(80, ge=10, le=400)) -> dict:
    """Aligned per-service time series powering the dashboard charts, plus
    anomaly markers (mapped to their window's bucket) and a health summary."""
    settings = get_settings()
    windows = db.list_windows(limit=buckets * 6)  # enough rows across services

    # Build the shared time axis from the most recent unique bucket starts.
    all_starts = sorted({w["bucket_start"] for w in windows})[-buckets:]
    keep = set(all_starts)
    idx = {start: i for i, start in enumerate(all_starts)}
    services = sorted({w["service"] for w in windows})

    def empty():
        return [None] * len(all_starts)

    series = {s: {"count": empty(), "error_rate": empty(), "latency_p95": empty()}
              for s in services}
    summary: dict[str, dict] = {}
    win_bucket: dict[int, str] = {}
    for w in windows:
        win_bucket[w["id"]] = w["bucket_start"]
        if w["bucket_start"] not in keep:
            continue
        i = idx[w["bucket_start"]]
        s = series[w["service"]]
        s["count"][i] = w["count"]
        s["error_rate"][i] = round(w["error_rate"], 4)
        s["latency_p95"][i] = round(w["latency_p95"], 1) if w["latency_p95"] else None
        summary[w["service"]] = {
            "count": w["count"], "error_rate": round(w["error_rate"], 4),
            "latency_mean": round(w["latency_mean"], 1) if w["latency_mean"] else None,
            "latency_p95": round(w["latency_p95"], 1) if w["latency_p95"] else None,
        }

    anomalies = []
    for a in db.list_anomalies(limit=60):
        bstart = win_bucket.get(a["window_id"])
        if bstart in keep:
            anomalies.append({
                "bucket_start": bstart, "service": a["service"],
                "score": a["score"], "severity": a["severity"],
                "method": a["method"], "category": a["category"],
                "explanation": a["explanation"], "ts": a["ts"],
            })

    return {
        "labels": all_starts,
        "services": services,
        "series": series,
        "summary": summary,
        "anomalies": anomalies,
        "alert_threshold": settings.alert_threshold,
    }


@app.get("/", response_class=HTMLResponse, tags=["dashboard"])
def dashboard(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "version": __version__,
            "llm_active": settings.llm_active,
            "llm_enabled": settings.llm_enabled,
        },
    )
