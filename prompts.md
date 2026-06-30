# Vibe Coding Audit Log

Project: SRE Event Watchdog
Tool: Claude Code (end-to-end, no manual edits)

---

### Entry 1 — 2026-06-30 (T0)

**Prompt (verbatim):**
> Lead Architect mode: ON. We are building a Python-based, API-first Intelligent Observability & Event Watchdog (SRE log anomaly detection service) using a free SQLite database and a dashboard.
>
> Rules:
> - No Manual Edits: You provide all logic and fixes. I will not edit any code.
> - Audit Log: You must maintain a file named prompts.md. After every turn, append to it the verbatim prompt I just gave you, a timestamp, and a one-line summary of what you changed.
> - Time-Check: Start a timer now. Goal is an MVP in 4-6 hours (max window 16h). Report 'Elapsed Time' at the end of every response.
>
> Before writing any code, do this:
> 1. Ask me up to 5 sharp clarifying questions about scope, the anomaly-detection approach, and what would most impress reviewers for a Forward Deployed / GenAI Engineer role.
> 2. Then write a concise SPEC.md describing the architecture: FastAPI app, SQLite storage, log ingestion endpoint, a hybrid anomaly detector (rolling z-score / EWMA baseline + scikit-learn Isolation Forest + an optional LLM-based log classifier that is feature-flagged so the app runs without an API key), a simulated webhook alerting mechanism with a local mock receiver, and a lightweight server-rendered Chart.js health-trends dashboard. Include a synthetic log generator so the whole thing is self-contained and demoable in one command.
> 3. Wait for my approval of SPEC.md before scaffolding code.
>
> Acknowledge and let's start.

**Summary of changes:** Started timer (T0 = 2026-06-30). Acknowledged Lead Architect mode + rules. Asked 4 clarifying questions; user chose: hybrid 3-layer detector, multi-feature signal (level+latency+service), GenAI-craft showcase focus, single-process Python demo. Drafted `SPEC.md` for approval.

---

### Entry 2 — 2026-06-30 (T0 + ~0:15)

**Prompt (verbatim):**
> Approved — proceed to scaffold. Two additions before you build:
>
> 1. The README is a graded deliverable and must sell this for a Forward Deployed / GenAI Engineer role. Plan a strong README with: a one-line value prop, an architecture diagram (ASCII or Mermaid), a clear explanation of WHY the hybrid stats+ML+LLM design (judgment, cost-gating), a copy-paste Quickstart (install → one command → open dashboard + /docs), a screenshot/GIF placeholder of anomalies firing, a "Vibe-coding process" section linking prompts.md, and a "Production hardening / what I'd do next" section. Also note in the README that the app runs fully local (SQLite, localhost) with synthetic data, so there are no cloud resources to provision or decommission.
>
> 2. Keep prompts.md rigorous: every prompt I give you, verbatim, with a timestamp and a one-line change summary. This is a required submission artifact.
>
> Now scaffold the full project structure and get a minimal app running (health endpoint + /docs reachable) as the first milestone, then stop and show me how to run it. Report Elapsed Time.

**Summary of changes:** Scaffolded full project structure (app package, mock_receiver, scripts, tests dirs); implemented config, SQLite layer + schema, pydantic models, FastAPI app with `/api/health` + `/docs` + dashboard placeholder, `__main__` entrypoint, mock receiver, requirements, `.env.example`, run scripts, and a graded-deliverable README. Verified app boots and health endpoint responds (Milestone 1).

---

### Entry 3 — 2026-06-30 (T0 + ~0:45)

**Prompt (verbatim):**
> Kill it and continue to the next milestone

**Summary of changes:** Killed stale process (PID 171316) holding port 8000. Built Milestone 2: log ingestion API (`POST /api/logs` single + batch), per-service time-bucket windowing with multi-feature extraction (count, error_rate, latency mean/p95/std), EWMA + z-score statistical detector, detection pipeline orchestrator with hybrid scoring + severity, anomaly persistence, and `GET /api/anomalies`. Added unit tests for windowing + stats detector + ingestion. Tuned EWMA alpha 0.3→0.4 for faster convergence within the demo window.

---

### Entry 4 — 2026-06-30 (T0 + ~1:20)

**Prompt (verbatim):**
> continue to the next milestone

**Summary of changes:** Built Milestone 3 — Layer 2 Isolation Forest detector (`app/detectors/iforest.py`): per-service rolling buffer, warmup gate, periodic refit, normalized [0,1] anomaly score via training-distribution sigmoid. Folded it into the pipeline's hybrid scoring (`score = max(stats, iforest)`, method records contributing layers, OR-of-layers anomaly flag). Added unit tests for the Isolation Forest layer and a multivariate-anomaly integration case. (Investigated 2 test failures: found+reverted a no-op StandardScaler — IF is invariant to monotonic per-feature transforms — and fixed the test to use realistic per-feature noise instead of tuning thresholds.)

---

### Entry 5 — 2026-06-30 (T0 + ~2:00)

**Prompt (verbatim):**
> continue to the next milestone

**Summary of changes:** Built Milestone 4 — alerting. Added `app/alerter.py` (httpx webhook delivery + per-service cooldown to prevent alert storms, persists delivery status). Wired it into the pipeline so anomalies at/above `alert_threshold` fire alerts outside the detection lock. Added `GET /api/alerts`. Added tests covering cooldown suppression and end-to-end delivery against the mock receiver (via Starlette TestClient — httpx 0.28 ASGITransport is async-only). Verified live cross-process: incident → anomaly → alert delivered to mock receiver.

---

### Entry 6 — 2026-06-30 (T0 + ~2:35)

**Prompt (verbatim):**
> continue to the next milestone

**Summary of changes:** Built Milestone 5 — synthetic log generator (`app/generator.py`): multi-service profiles with realistic noisy normal traffic, four incident scenarios (error_burst, latency_regression, dependency_outage, traffic_spike), startup history pre-seeding (backdates ~45 buckets so both detectors are warm within seconds), and a background thread feeding the pipeline in real time. Wired generator into the app lifespan (gated by `WATCHDOG_GENERATOR`) and added `POST /api/demo/inject` to trigger incidents on demand. Added generator unit tests. Fixed a 10x perf issue (thread-local SQLite connection pool; test suite 159s→15s) and tuned detection thresholds via measurement (z 3→6, iforest 0.85→0.9) cutting healthy false positives 29→2 with no false alerts.

---

### Entry 7 — 2026-06-30 (T0 + ~3:20)

**Prompt (verbatim):**
> continue to the next milestone

**Summary of changes:** Built Milestone 6 — dashboard. Added `GET /api/stats/timeseries` (server-side aligned per-service series for volume/error-rate/latency-p95, anomaly markers with bucket alignment, per-service health summary) and `db.list_windows`. Rewrote `templates/dashboard.html` as a live Chart.js dashboard: 4 trend charts (volume, error rate, latency p95, anomaly score with alert-threshold line), per-service health cards, recent-anomalies and recent-alerts panels with severity badges, auto-refresh polling, and a one-click "inject incident" demo control. Expanded `static/style.css`. Added a measured "Detection tuning" note to the README.

---

### Entry 8 — 2026-06-30 (T0 + ~3:45)

**Prompt (verbatim):**
> continue to the next milestone

**Summary of changes:** Built Milestone 7 — the GenAI showcase: a feature-flagged LLM root-cause classifier (`app/detectors/llm.py`) using the official Anthropic SDK with **strict tool use** for guaranteed structured output (category / probable_root_cause / severity / recommended_action / confidence). Consulted the claude-api skill for authoritative model IDs + tool-use syntax. The layer is **cost-gated** (only runs on already-flagged anomalies), runs **outside the detection lock** (network I/O never blocks ingestion), and has a **clean no-key fallback** (disabled → returns None, app behaves identically). Added `db.recent_logs` + `db.update_anomaly_enrichment`, wired enrichment into the pipeline, and exposed `category`/recommended-action on anomalies + alerts + dashboard. Added tests with an injected fake client (no key/network) covering the enrich path and the disabled fallback.

---

### Entry 9 — 2026-06-30 (T0 + ~4:15)

**Prompt (verbatim):**
> Run the final polish pass: refresh the pytest count badge / README test count, and run /code-review over the full diff and fix anything genuinely worth fixing (don't gold-plate — we're feature-complete and inside budget). Skip the Playwright screenshot; I'll capture the demo image manually.
>
> After that, do two packaging things:
> 1. Add a short "Vibe-Coding Process" section to the README that highlights 3-4 of the real debugging decisions from this build (the EWMA alpha tuning, the Isolation Forest constant-feature root cause, the z_threshold false-positive measurement where you left the ~1% IF residual rather than fake a zero, and the httpx ASGITransport fix) — framed as evidence of engineering judgment, linking to prompts.md.
> 2. Confirm prompts.md is complete and well-formatted with every prompt, timestamp, and change summary.
>
> Report Elapsed Time when done.

**Summary of changes:** Final polish (Milestone 8). Removed stray live-check log files. Refreshed README Testing section with a 34-passing badge + accurate coverage description. Expanded the README "Vibe-coding process" with an "Engineering judgment" subsection covering the 4 real debugging decisions (EWMA alpha tuning, Isolation Forest constant-feature root cause + reverted no-op StandardScaler, measured z_threshold FP reduction with the honest ~1% IF residual, httpx 0.28 ASGITransport fix), linked to this log. Ran a high-effort workflow-backed code review (23 agents) over the full diff → 24 verified findings / 15 distinct defects (1 refuted). Fixed the 8 genuinely worth fixing: (1) alerter cooldown armed before delivery → arm only on success; (2) stats EWMA absorbing sustained incidents → freeze baseline during a confirmed anomaly; (3) stats None-latency coerced to 0.0 corrupting the baseline → skip missing features; (4) Isolation Forest absorbing sustained incidents → keep flagged windows out of the training buffer; (5) hybrid max-score inflated by a non-flagging IF layer → combine only flagging layers; (6) LLM "noise" verdict ignored for alerting → suppress the page; (7) generator.status() racing the tick thread → snapshot under lock; (8) live-tick logs timestamped a full bucket into the future → spread over the tick interval. Added 4 regression tests (34 total, all green). Deliberately deferred 7 lower-value items (severity-escalation cooldown, variance floor, late-log misattribution, dashboard 0.0 truthiness, per-row commits, sync startup seeding) as MVP trade-offs / already-mitigated. Verified live: detection + alerting end-to-end, health endpoint survives concurrent load with no 500s. Committed the result.

---

### Entry 10 — 2026-06-30 (T0 + ~5:05)

**Prompt (verbatim):**
> push it and open a PR

**Summary of changes:** No remote existed (local-only repo). Confirmed two user decisions: public visibility, and seed-empty-main + PR-the-project structure. Created public GitHub repo `mallimatla/sre-event-watchdog`, seeded an empty `main` root commit, re-rooted `feat/sre-event-watchdog-mvp` onto it (so the PR shows the whole project as a reviewable diff with a common ancestor), pushed both branches, and opened PR #1 (feat → main). Added `*.log` to `.gitignore`; no runtime artifacts committed.

---

### Entry 11 — 2026-06-30 (T0 + ~5:15)

**Prompt (verbatim):**
> add a CI workflow with lint and pytest

**Summary of changes:** Added GitHub Actions CI (`.github/workflows/ci.yml`): ruff lint + pytest on a Python 3.11/3.12 matrix, triggered on push + PR to `main`, with pip caching. Added `ruff.toml` (E/F/W/I rules, line-length 100, `tests/*` E402 ignore for the env-before-import pattern). Fixed the lint findings to get a green check: ruff auto-sorted imports in 4 test files and I wrapped one over-length assertion in `test_llm.py`. Verified locally: `ruff check` clean, 34 tests pass. Added a CI status badge to the README. Committed + pushed to update PR #1. CI run went green on both Python versions.

---

### Entry 12 — 2026-06-30 (T0 + ~5:30)

**Prompt (verbatim):**
> Two final deliverables before I package the submission:
>
> 1. Generate the AI presentation deck the challenge requires. Create docs/DECK.md as a Marp markdown deck (~7-8 slides): title/value-prop, the problem (SRE alert fatigue / late incident detection), architecture (the 3-layer hybrid detector + alerting + dashboard, with the Mermaid diagram), the GenAI layer (structured tool-use root-cause output, cost-gated, no-key fallback), a "live demo" slide describing the one-command flow, results/metrics (34 tests, 2-layer detection, <demo numbers>), the vibe-coding process (no manual edits, prompts.md audit, the 4 engineering-judgment debugging calls), and a "production hardening / what's next" slide. Keep it crisp and reviewer-facing. Also add a one-line note in the README pointing to docs/DECK.md and how to render it (marp).
> 2. Tell me the exact command to run the app so I can open the dashboard in my browser and capture a screenshot for docs/demo.gif (or a PNG) — I'll do that part manually.
>
> Report Elapsed Time.

**Summary of changes:** Created `docs/DECK.md` — an 8-slide Marp deck (title/value-prop, problem, architecture w/ Mermaid, hybrid-design judgment, GenAI layer, one-command live demo, results/metrics, vibe-coding process w/ the 4 debugging calls, production-hardening), styled to match the dashboard theme. Added a one-line README pointer to the deck with the `marp` render command. Provided the exact run command for the manual screenshot capture.

---

### Entry 13 — 2026-06-30 (T0 + ~5:45)

**Prompt (verbatim):**
> merge the PR

**Summary of changes:** Verified CI green on the latest PR commit (ruff + pytest on Python 3.11 & 3.12) and the PR `MERGEABLE`/`CLEAN`, then merged PR #1 into `main` with a merge commit (preserving the build history) and deleted the feature branch. Confirmed clean working tree; main's post-merge CI run kicked off. Appended this final audit entry.

---

### Entry 14 — 2026-06-30 (T0 + ~5:50)

**Prompt (verbatim):**
> I've placed the dashboard screenshot at docs/demo.png. Update the README image reference from docs/demo.gif to docs/demo.png (and fix any other demo.gif references), confirm the image renders, then commit and push that docs-only change to main. After that, give me a final submission checklist confirming every required deliverable exists and where: (1) public GitHub repo URL, (2) prompts.md, (3) the deck docs/DECK.md, (4) README with the screenshot, (5) the explicit "fully local, no cloud resources to decommission" confirmation. Report final Elapsed Time.

**Summary of changes:** User added `docs/demo.png` (3840×4417 dashboard screenshot showing an injected incident — populated hybrid stats+iforest anomalies, all alerts `sent`). Updated the README Demo section to embed `docs/demo.png` and removed the obsolete GIF placeholder text; confirmed no other `demo.gif` references remain (the one in prompts.md is a verbatim audit quote, left intact). Committed README + the PNG + this entry directly to `main` (docs-only, repo already merged) and pushed. Provided the final submission checklist.
