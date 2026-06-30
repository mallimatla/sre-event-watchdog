# SPEC — SRE Event Watchdog

**Intelligent Observability & Event Watchdog**: an API-first service that ingests application logs, detects anomalies with a hybrid statistical + ML + LLM pipeline, fires webhook alerts, and renders a live health-trends dashboard. Fully self-contained: one command runs the API, a mock alert receiver, and a synthetic log generator with no external dependencies or API keys required.

> Scope decisions (from clarifying Q&A): **hybrid 3-layer detector**, **multi-feature signal (level + latency + service)**, **GenAI-craft showcase focus**, **single-process Python demo**.

---

## 1. Goals & Non-Goals

**Goals**
- API-first ingestion of structured logs (HTTP `POST`).
- Hybrid anomaly detection over rolling windows: (1) statistical baseline, (2) Isolation Forest, (3) optional LLM classifier/root-cause summarizer.
- Alerting via webhook to a local mock receiver; alerts persisted and queryable.
- Server-rendered dashboard with Chart.js showing volume, error rate, latency, anomaly scores, and recent alerts.
- One-command, self-contained demo with a synthetic log generator producing both normal traffic and injected incidents.

**Non-Goals (MVP)**
- Auth/multi-tenancy, horizontal scaling, real message queues.
- Persisting beyond local SQLite. No cloud deployment.
- Real third-party alert integrations (Slack/PagerDuty) — simulated via mock receiver, but designed so a real webhook URL drops in.

---

## 2. Architecture Overview

```
                       ┌─────────────────────────────────────────────┐
                       │              FastAPI app (app/)               │
  synthetic   POST     │  ┌────────────┐   ┌──────────────────────┐   │
  generator ─────────► │  │ Ingest API │──►│  Detection Pipeline  │   │
  (background task)    │  └─────┬──────┘   │  1. Stats (z/EWMA)   │   │
                       │        │          │  2. IsolationForest  │   │
  curl / clients ────► │        │          │  3. LLM classifier*  │   │
                       │        ▼          └──────────┬───────────┘   │
                       │   ┌─────────┐                │ anomaly       │
                       │   │ SQLite  │◄───────────────┤               │
                       │   │ (data/) │   logs/windows/anomalies/alerts│
                       │   └─────────┘                ▼               │
                       │   ┌──────────────┐    ┌─────────────┐        │
   browser ──────────► │   │ Dashboard    │    │ Alerter     │──webhook──► Mock
   (HTML + Chart.js)   │   │ (Jinja2)     │    │ (httpx)     │        │   Receiver
                       │   └──────────────┘    └─────────────┘        │   (FastAPI,
                       └─────────────────────────────────────────────┘   same proc)
  * LLM layer feature-flagged: runs only if WATCHDOG_LLM_ENABLED + ANTHROPIC_API_KEY set.
```

Single process via `python -m app` (uvicorn). The mock receiver runs as a second uvicorn app on a different port, started by the same `run` script. Synthetic generator runs as an in-process background task, toggleable via env/flag.

---

## 3. Tech Stack

- **Python 3.11+**
- **FastAPI** + **uvicorn** — API and dashboard server.
- **SQLite** via stdlib `sqlite3` (thin repository layer; no ORM to keep it lean).
- **scikit-learn** — Isolation Forest. **numpy** for stats.
- **Jinja2** — server-rendered dashboard; **Chart.js** via CDN (with a vendored fallback note).
- **httpx** — webhook delivery + synthetic generator client.
- **anthropic** SDK — optional LLM layer (lazy import; absent key → cleanly skipped).
- **pydantic** — request/response models.
- **pytest** — unit tests for detectors and ingestion.

---

## 4. Data Model (SQLite)

`data/watchdog.db`, created on startup via `schema.sql`.

- **logs** — `id, ts (ISO8601), service, level (DEBUG/INFO/WARN/ERROR), message, latency_ms (REAL), raw_json`. Indexed on `(service, ts)`.
- **windows** — aggregated per service per time-bucket: `id, service, bucket_start, bucket_end, count, error_count, error_rate, latency_mean, latency_p95, latency_std`.
- **anomalies** — `id, ts, service, window_id, score (0-1), method (stats|iforest|llm|hybrid), features_json, explanation, severity (low/med/high)`.
- **alerts** — `id, anomaly_id, ts, status (sent|failed|mock_ack), target_url, payload_json, response_json`.

---

## 5. Detection Pipeline (the core)

Logs are aggregated into fixed time-buckets (default **10s** for snappy demo; configurable) per service. Each completed window produces a feature vector:

`[count, error_rate, latency_mean, latency_p95, latency_std]`

**Layer 1 — Statistical baseline (always on)**
- Rolling **EWMA** mean + variance per feature per service (online, no retrain).
- **z-score** of each feature vs. EWMA baseline; window flagged if any |z| > `Z_THRESHOLD` (default 3.0).
- Cheap, interpretable, cold-start friendly — produces the first useful signal immediately.

**Layer 2 — Isolation Forest (ML)**
- `sklearn.ensemble.IsolationForest` trained on a rolling buffer of recent windows (warm-up after N windows, periodic refit).
- Produces `score_samples` → normalized anomaly score in [0,1].
- Catches multivariate/contextual anomalies the per-feature z-score misses.

**Layer 3 — LLM classifier (feature-flagged, the GenAI showcase)**
- Triggered **only** when Layers 1/2 flag a window (cost-aware — LLM is the expensive layer, gated behind cheap detectors).
- Sends the anomalous window's aggregates + a sample of raw log lines to Claude (`claude-haiku-4-5` default for speed/cost).
- **Structured output** (tool-use / JSON schema): `{ category, probable_root_cause, severity, recommended_action, confidence }`.
  Categories e.g. `deployment_regression | dependency_outage | traffic_spike | resource_exhaustion | noise`.
- **Graceful fallback**: if `WATCHDOG_LLM_ENABLED=false` or no key, layer is skipped; anomaly still recorded with stats/iforest explanation. App behavior is identical minus the enrichment, so it always runs.

**Hybrid scoring**
- Final `score = max(stat_score, iforest_score)`; severity from score bands. LLM (if present) overrides/enriches `category`, `explanation`, `severity`, and `recommended_action`. `method` records which layers contributed.

---

## 6. Alerting

- On a persisted anomaly above `ALERT_THRESHOLD`, the **Alerter** POSTs a JSON payload to `WATCHDOG_WEBHOOK_URL` (default = local mock receiver) via httpx.
- Payload: anomaly id, service, score, severity, features, LLM explanation/root-cause (if any), dashboard link.
- **Mock receiver** (`mock_receiver/`): tiny FastAPI app on a separate port that logs/echoes received alerts and exposes `GET /received` for the demo and tests. Simulates an external system; swapping in a real Slack/PagerDuty URL needs only an env change.
- De-dup/cooldown: per-service alert cooldown window to avoid alert storms.

---

## 7. API Surface

- `POST /api/logs` — ingest one log or a batch. Body validated by pydantic. Triggers windowing/detection.
- `GET  /api/anomalies` — list recent anomalies (filter by service/severity/time).
- `GET  /api/alerts` — list fired alerts + delivery status.
- `GET  /api/health` — service liveness + pipeline stats (logs ingested, windows, anomalies, LLM enabled?).
- `GET  /api/stats/timeseries` — JSON timeseries (volume, error_rate, latency, anomaly score) powering the dashboard charts.
- `POST /api/demo/inject` — manually inject an incident (latency spike / error burst) for live demoing.
- `GET  /` — server-rendered dashboard (HTML).

Mock receiver: `POST /webhook`, `GET /received`.

---

## 8. Dashboard

- Single Jinja2 page, Chart.js from CDN, auto-refresh (poll `/api/stats/timeseries` every few seconds).
- Charts: **log volume**, **error rate**, **latency mean/p95**, **anomaly score** with threshold line; markers where anomalies fired.
- Panels: **service health summary**, **recent anomalies** (with LLM root-cause/category badges), **recent alerts** + delivery status, **LLM layer status** (enabled/disabled) so reviewers see the feature flag at work.

---

## 9. Synthetic Log Generator

- In-process background task (`generator.py`) that emits realistic multi-service logs (level + latency + service) via the ingest path.
- **Normal regime**: low error rate, stable latency distribution per service.
- **Incident injection**: scheduled/random scenarios — error burst, latency regression (deploy), dependency outage (error_rate + latency), traffic spike — so the dashboard visibly lights up and alerts fire within ~1 minute of startup.
- Controlled by env flags (`WATCHDOG_GENERATOR=true`, rate, incident schedule).

---

## 10. Configuration (env-driven, sane defaults)

`WATCHDOG_DB_PATH`, `WATCHDOG_BUCKET_SECONDS=10`, `Z_THRESHOLD=3.0`, `IFOREST_WARMUP=30`, `ALERT_THRESHOLD=0.7`, `ALERT_COOLDOWN_SECONDS=30`, `WATCHDOG_WEBHOOK_URL`, `WATCHDOG_GENERATOR=true`, `WATCHDOG_LLM_ENABLED=false`, `ANTHROPIC_API_KEY`, `WATCHDOG_LLM_MODEL=claude-haiku-4-5`. Loaded via pydantic-settings + optional `.env`.

---

## 11. Project Layout

```
sre-event-watchdog/
├── app/
│   ├── __main__.py          # entrypoint: starts API (+ optional generator)
│   ├── main.py              # FastAPI app, routes, dashboard
│   ├── config.py            # pydantic-settings
│   ├── db.py                # sqlite repository layer
│   ├── schema.sql
│   ├── models.py            # pydantic models
│   ├── windowing.py         # bucket aggregation + feature extraction
│   ├── detectors/
│   │   ├── stats.py         # EWMA + z-score
│   │   ├── iforest.py       # Isolation Forest wrapper
│   │   └── llm.py           # feature-flagged Claude classifier (structured output)
│   ├── pipeline.py          # orchestrates 3 layers + hybrid scoring
│   ├── alerter.py           # webhook delivery + cooldown
│   ├── generator.py         # synthetic logs + incident injection
│   ├── templates/dashboard.html
│   └── static/              # css, optional vendored chart.js
├── mock_receiver/main.py    # mock webhook receiver (separate port)
├── tests/                   # pytest: detectors, windowing, ingestion, alerter
├── scripts/run.(sh|ps1)     # one-command demo launcher
├── requirements.txt
├── .env.example
├── README.md
├── SPEC.md
└── prompts.md
```

---

## 12. One-Command Demo

`scripts/run.ps1` (Windows) / `scripts/run.sh`:
1. Create venv + install `requirements.txt` (first run only).
2. Launch mock receiver (port 8001).
3. Launch watchdog API + dashboard (port 8000) with generator enabled.
4. Print URLs: dashboard `http://localhost:8000/`, health, mock receiver `/received`.

Within ~1 minute: normal traffic flows, an injected incident trips the detectors, an alert POSTs to the mock receiver, and the dashboard charts spike. With `WATCHDOG_LLM_ENABLED=true` + key, anomalies gain LLM root-cause categorization.

---

## 13. Testing & Quality

- **Unit**: EWMA/z-score math, Isolation Forest scoring shape, windowing aggregation, alerter cooldown, ingestion validation, LLM-disabled fallback path.
- **Integration**: ingest synthetic incident → anomaly persisted → alert delivered to mock receiver (`GET /received` asserts).
- LLM layer tested with a mock client (no network/key in CI).

---

## 14. Build Plan & Time Budget (target 4–6h)

1. Scaffold + config + DB + ingest API + models. (~45m)
2. Windowing + stats detector + pipeline skeleton + persistence. (~60m)
3. Isolation Forest layer + hybrid scoring. (~45m)
4. Alerter + mock receiver + cooldown. (~30m)
5. Synthetic generator + incident scenarios. (~40m)
6. Dashboard (Jinja2 + Chart.js) + timeseries API. (~60m)
7. LLM layer (structured output + fallback) — the GenAI showcase. (~45m)
8. Tests, README, one-command run script, polish. (~45m)

---

## 15. What This Demonstrates (FDE / GenAI Engineer signal)

- **GenAI craft**: cost-gated LLM layer, structured tool-use output, clean no-key fallback, prompt design for root-cause reasoning.
- **SRE/ML judgment**: layered detection (cheap→expensive), online stats + ML, golden-signal awareness.
- **Product/engineering**: API-first design, live dashboard, one-command reproducible demo, tests, audit log.
```

> **Decision needed:** Approve this SPEC as-is, or tell me what to change. I will not scaffold code until you say go.
