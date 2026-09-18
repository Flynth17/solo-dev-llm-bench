# Solo Dev BenchLLM — Architecture

Implementation-grounded overview of how **Solo Dev BenchLLM** is built today. This
document describes what the code **does**, not what a future roadmap item plans to do.
Where a capability is planned or research-only, it is explicitly labelled so it can never
be mistaken for delivered behaviour.

> Canonical authority: [`docs/ROADMAP.md`](ROADMAP.md). Git history is implementation
> evidence; it is not the architecture. This file is derived from the current source on
> branch `feat/benchmark-v2`.

---

## 1. Scope and status legend

Every statement below is tagged with one of three statuses:

| Status | Meaning |
|--------|---------|
| **DELIVERED** | Implemented, persisted, and reachable through a live route or CLI entry point today. |
| **PLANNED** | Described in the roadmap but has no runner, API, persistence, or UI in the current source. Do not treat as available. |
| **RESEARCH** | A concept only. No design, corpus, scoring method, or implementation exists and none is finalised. |

A fourth implicit category — **retired** — covers products that were removed during the
M1/M2/M3 retirements (legacy Prompt Manager, Task Manager, Results product). Those are
documented here only to state that they no longer exist; their concepts must not be
resurrected.

---

## 2. System overview

Solo Dev BenchLLM is a **local-only FastAPI web application** plus a set of Python
benchmark modules that drive **LM Studio's native OpenAI-compatible v1 API**. There is no
separate backend service, no database server, and no network frontend. The dashboard is a
set of static HTML/JS pages served from `bench_llm/static/`; all interactivity flows
through FastAPI routes.

Conceptual data flow:

```
Browser (static SPA pages)
        │  fetch / POST JSON
        ▼
FastAPI app  (src/main.py)
        │  route handlers (src/routes/*)
        ├──▶ Standard Speed launcher   (src/v2_speed_suite_runner.py)   ── subprocess ──▶ rows → ResultsStore
        ├──▶ Workflow launcher          (src/v2_workflow_runner.py)      ── subprocess ──▶ V2 artifact (data/v2_runs/*.json)
        ├──▶ Model lifecycle            (src/model_lifecycle.py)         ──▶ LM Studio native v1 API
        ├──▶ Ranking read model         (src/ranking_read_model.py)     ── reads persisted evidence only
        ▼
Persistence: SQLite (primary) + CSV (compatibility mirror)  →  Read models / result pages
```

Two heavyweight benchmark suites each run in their **own OS process** and are guarded by a
single shared in-memory concurrency guard so they never contend for the same local model.
Completion is proven **durably** (result rows or an artifact file), not by a live process —
so a finished run is still reported as completed after a FastAPI restart.

---

## 3. Application / API architecture

### Entry point

- `bench_llm/src/server_launcher.py` — dev launcher. Sets `sys.path`, ensures the `data/`
  directory exists, and starts uvicorn on **`127.0.0.1:8000`** (loopback only) with file
  reload scoped to `src/*.py`. This is what `bench_llm/start_bench.bat` invokes on Windows.
- `bench_llm/src/__main__.py` — CLI entry point (`python -m src`) exposing three
  subcommands: `v2-suite <suite>`, `v2-run` (all five suites), and `v2-speed`. Used by the
  launchers to run benchmarks in detached child processes.
- `bench_llm/src/main.py` — FastAPI application construction. Creates `app = FastAPI(...)`,
  mounts `/static`, registers every route router, and defines the HTML page routes.

### Routing

Routes are organised as small `APIRouter`s under `bench_llm/src/routes/`, each paired with
a concern:

| Module | Responsibility |
|--------|----------------|
| `routes/config.py` | `GET/POST /api/config` — read/write `config/settings.json`. |
| `routes/models.py` | Model discovery + load/unload lifecycle (`/api/models*`). |
| `routes/results.py` | Standard Speed history surface (`/api/results`). |
| `routes/v2_results.py` | Read-only single Workflow run view (`/api/v2/results/{run_id}*`). |
| `routes/v2_workflow.py` | Workflow suite launcher + status (`/api/v2/workflow/*`). |
| `routes/v2_speed.py` | Standard Speed launcher + status + single-run read (`/api/v2/speed/*`, `/api/speed/*`). |
| `routes/ranking.py` | Read-only canonical ranking/aggregation API (`/api/ranking*`). |

`main.py` wires them via `app.include_router(...)` and serves four HTML surfaces:

| Route | Page | Owned by |
|-------|------|----------|
| `GET /` | `static/index.html` | Dashboard / launcher |
| `GET /results` | `static/results.html` | Standard Speed history |
| `GET /speed/results/{run_id}` | `static/speed-result.html` | Single Standard Speed run |
| `GET /v2/results/{run_id}` | `static/v2-result.html` | Single Workflow run |

### Backend / frontend boundary

The boundary is **route → read model**, not route → database. Routes never query SQLite or
CSV directly for presentation; they delegate to validated, read-only read models
(`load_speed_run_by_id`, `load_v2_result`, `build_ranking`). Static pages derive their data
from these APIs and never re-implement benchmark semantics. This keeps a single source of
truth for metric interpretation (e.g. Standard Speed point values are identical on the
history page and the dedicated run page).

### Configuration boundary

`bench_llm/src/config_loader.py` is the sole reader/writer of `bench_llm/config/settings.json`
(`lm_studio_url`, `model`). All other modules obtain configuration through
`load_config()`. The default backend URL (`http://localhost:1234`) is validated against the
backend allow-list before it is ever used or persisted (see §6).

### Testing

Tests live under `bench_llm/tests/` (41 files) governed by `bench_llm/pytest.ini`
(`testpaths = tests`, `python_files = test_*.py`). They run **GPU-free and in-memory** where
possible; a subset exercises the model-backed runners against a local LM Studio endpoint.
Run from `bench_llm`:

```bash
python -m pytest
```

Coverage spans isolation, fail-closed execution-boundary behaviour, timeouts, backend-URL
policy, LAN boundary, and result-compatibility contracts. Workflow suite fixtures (the
deterministic denominators exercised by the runner) live under `bench_llm/tasks/`. The test
suite is read-only with respect to benchmark mechanics — it validates existing behaviour rather
than driving production modules.

---

## 4. Model lifecycle and LM Studio boundary — DELIVERED

Model discovery, metadata, load, and unload are implemented in `bench_llm/src/model_lifecycle.py`,
a thin wrapper over **LM Studio's native v1 REST API** (`GET /api/v1/models`,
`POST /api/v1/models/load`, `POST /api/v1/models/unload`). It performs no benchmark logic.

- **Discovery** reuses `src.benchmark.fetch_models` (normalised model list + state).
- **Selection and loading are separate actions.** Loading applies only the *standard*
  context target (`min(model.max_context_length, 262144)`); arbitrary context / batch /
  flash-attention / MTP / expert / KV-cache overrides are rejected at the adapter layer.
- **The runtime is authoritative.** After any mutation the adapter re-reads
  `GET /api/v1/models` and reports what the instance actually holds — it never claims a
  requested value was applied.
- **No silent eviction.** Loading while a *different* LLM is loaded returns `409`; an
  already-loaded model is not reloaded. Unload targets the real `instance_id` (never
  assumes `key == instance_id`) and errors on ambiguous multiple instances.

Lifecycle mutations are gated by `_reject_if_benchmark_active()` in `routes/models.py`, which
defers to the shared concurrency guard (`standard_run_guard`), so a model cannot be loaded or
unloaded mid-run.

**Backend boundary:** LM Studio is the **only** supported backend — Ollama, vLLM, and other
providers are explicitly not supported. All backend URLs default to loopback and are
validated by `backend_url_policy.py` before any outbound request (see §6).

---

## 5. Benchmark architecture

### 5.1 Standard Speed — DELIVERED

`bench_llm/src/v2_speed_suite_runner.py` executes the fixed **standard Speed contract**:
three binary context-pressure points — **8K / 16K / 32K** (8192 / 16384 / 32768 tokens) —
measuring TTFT, full-prefill throughput, and generation/decode throughput.

- **Orchestration:** `run_speed_suite()` builds a deterministic, content-insensitive
  payload whose input load targets each canonical point, wraps the authoritative
  `src.benchmark.run_benchmark` engine for request + timing + token capture, and persists one
  durable row per attempted point to the shared `ResultsStore`.
- **Repeatability (evidence-first):** each supported point records exactly **one cold**
  (cache-busted full-prefill) plus **two warm** (`warm_a` / `warm_b`) runs, persisted as
  independent rows tagged by `speed_run_stage`. Cold and warm are never averaged: prefill
  throughput stays a **cold-only** signal; generation uses the warm repeatability pair's mean.
- **Calibration (metric v3):** payloads are sized at runtime so actual input tokens land
  within 2% of the canonical target, using a cache-busting deterministic prefix that defeats
  LM Studio's longest-common-prefix KV-cache reuse. A new metric version (`v3`) is persisted
  per run to distinguish corrected full-prefill numbers from legacy cached-TTFT artifacts.
- **Unsupported handling:** when a model's effective loaded context is below a target, the
  point is marked `unsupported` and that status is persisted — the target is never silently
  shrunk.
- **Launch + status:** `routes/v2_speed.py` exposes execution plumbing only (`POST
  /api/v2/speed/run`, `GET /api/v2/speed/runs/{id}/status`) plus a read-only single-run view
  (`GET /api/speed/runs/{id}`). Completion is proven by durable rows, not a live process.

### 5.2 Workflow — DELIVERED

Workflow is a **deterministic developer-workflow correctness/reliability** benchmark: no LLM
judge, no subjective scoring, validation via deterministic detectors (including Java
compilation). It measures correctness, **not** general intelligence.

- **Orchestration split:** `src/v2_workflow_runner.py` is *execution plumbing only* — it
  spawns the locked runner as a separate OS process (`python -m src v2-run`) and tracks its
  lifecycle. It explicitly **never imports or calls** the legacy executor
  (`src.v2_quality_executor::run_v2_quality_live`). The actual benchmark logic lives in the
  locked suite runner `src/v2_quality_suite_runner.py`.
- **Deterministic denominators:** five suites run against fixed, immutable denominators —
  python 58, java 52, markdown 20, evidence 30, drift 6 = **166 checks total** (`TOTAL_DENOMINATOR`).
  A partial validation can never remove checks from the denominator (e.g. `markdown 19/20`
  stays `19/20`).
- **Isolation:** each suite runs in its own standalone OS process; a shared config fingerprint
  keeps identity identical across suites and runs.
- **Persistence:** completed runs are written atomically to
  `data/v2_runs/<run_id>.json` (see §7). Completion is proven by `try_load(run_id)` returning
  a valid artifact — no database, no queue.
- **Launch + status:** `routes/v2_workflow.py` exposes the launcher API; single-run detail is
  served via `routes/v2_results.py` (`GET /api/v2/results/{run_id}`) and the `/v2/results/{run_id}` page.

### 5.3 Context — DELIVERED

Context measures **how model capability changes as usable context grows** at the fixed points
**15K / 30K / 60K / 120K / 180K / 240K where supported**. It is a quality-retention /
degradation family: exact-match fact recall (quality) and TTFT/prefill/degradation growth
(performance), with capacity limits surfaced as gaps. It is deliberately **not** Standard Speed,
prompt/prefill throughput, Workflow/Agentic, AI Intelligence, composite scoring, or the final
Results UI (that is RM-26-AA-0018).

- **Deterministic corpus (`src/context_corpus/`):** a fixed, known fact set with stable expected
  values. `builder.build_prompt()` grows cache-busting deterministic filler until the prompt
  reaches the requested size and embeds the facts block; per-run salt defeats cross-run KV-cache
  reuse without changing gradeable content. `scoring.point_score()` is exact-match only, returns
  `None` (never `0`) when nothing is gradeable, and degradation/retention math guards against
  divide-by-zero so an unsupported point can never be coerced to a numeric score.
- **Execution plumbing only (`src/v2_context_suite_runner.py`):** orchestrates the locked suite,
  builds deterministic payloads (`temperature=0`, `reasoning=off`), and persists one durable row
  per attempted point. It explicitly **never imports or calls** the legacy executor — matching
  the Workflow isolation contract.
- **Persistence (`src/v2_context_artifact.py`):** atomic, schema-versioned artifacts keyed by a
  unique `ctx-*` run id; each supported point persists exactly one independent row. Unsupported
  points persist as null-scored gaps with an explicit reason — never zero, never rescaled.
- **Ownership isolation:** every artifact records a per-run configuration fingerprint and model
  identity. The read model validates that the baseline points at a *persisted* supported point
  and refuses to "repair" inconsistent data — so one run's evidence can never attach to another
  model/config (the class of bug Standard Speed had).
- **Read model (`src/v2_context_read_model.py`):** projects authoritative per-point data verbatim
  for future UI; surfaces `supported_point_count`, `gap_point_count`, baseline, and per-point
  degradation/retention. It never recomputes scores.
- **Baseline + degradation:** the baseline is the smallest *supported* point; degradation is
  computed over authoritative per-point scores so a zero-correct deep point reads as real loss,
  not a rescale.
- **Launch + status:** `routes/context.py` exposes execution plumbing only (`POST /api/context/run`,
  `GET /api/context/runs/{id}/status`) plus a read-only single-run view (`GET /api/context/runs/{id}`).
  Completion is proven by a durable artifact, not a live process. A temporary result-page shell in
  `main.py` exists only so the launcher's `result_url` deep-link resolves; the full degradation UI
  is RM-26-AA-0018.
- **CLI:** `python -m src context-suite` runs the family as a fresh process (see §3).
- **Tests:** `tests/test_context_corpus.py`, `tests/test_context_suite_runner.py`,
  `tests/test_context_read_model.py`, `tests/test_context_route.py`. Full suite: 623 passed.

### 5.4 AI Intelligence — RESEARCH · not implemented

Nothing is implemented: no runner, corpus, API, persistence, UI, or scoring design exists and
none is finalised. The intended distinction from Workflow is a genuinely **discriminative
capability** benchmark for cases where Workflow saturates — targeting reasoning, synthesis,
planning, judgement, problem-solving, instruction interpretation, ambiguity handling, and
multi-step decision quality — explicitly *not* a duplicate of Workflow and never reduced to a
single "universal intelligence" number.

---

## 6. Execution and security boundaries

Security posture is documented authoritatively in [`docs/SECURITY.md`](SECURITY.md); this
section states what the implementation currently **guarantees** (and, importantly, what it does
*not*).

### Generated-code execution boundary

`bench_llm/src/execution_boundary.py` enforces: *"The benchmark tests the model; the model
does not inherit trust from the benchmark host."* Model-generated Python or Java is **never**
executed in-process and never with the full host environment:

- **Secret-stripped environment** — only an explicit allow-list of benign variables
  (`SYSTEMROOT`, `TEMP`, `TMP`, `NUMBER_OF_PROCESSORS`) may be forwarded; no `HOME`/credentials,
  never `os.environ` wholesale.
- **Isolated disposable working directory** — only the files required by the individual case;
  it cannot traverse up into the benchmark repository by relative path.
- **Hard wall-clock timeout** (`DEFAULT_TIMEOUT_SECONDS = 60`).
- **No console window** on Windows (`CREATE_NO_WINDOW`); **fails closed** — if the secure
  runner cannot start (`ExecutionBoundaryError`) it is reported as an infrastructure failure,
  with no silent fall back to unrestricted host execution.

**Honest platform boundary:** by default this runs generated code as a **host subprocess**
with the controls above. On a typical Windows dev host there is no dependency-free OS-level
network-egress block or absolute-file-system sandbox for an arbitrary child process (that
requires a container/namespace or a Windows Job Object). This is therefore documented as an
**uncontrolled boundary — not a sandbox**. The `ExecutionBoundary` interface is decoupled so a
stricter runner can be swapped in later without touching the validators.

### Backend URL allow-list (SSRF boundary)

`bench_llm/src/backend_url_policy.py` makes the API *not* act as a generic HTTP requester toward
arbitrary destinations. URLs default to loopback (`127.0.0.1` / `localhost`) and are validated
**before** they are persisted or used for any outbound request. Matching is an explicit
allow-list (no DNS resolution, no "block known-bad"); only hosts named in the allow-list can be
contacted. `trusted_backend_hosts` in `settings.json` may extend it; a missing/unreadable config
falls back to loopback-only. Malformed URLs and disallowed schemes are rejected.

### Concurrency guard

`bench_llm/src/standard_run_guard.py` is a single in-memory guard shared by both heavyweight
suites. It tracks launched child-process handles so Workflow↔Speed reject overlapping launches
**bidirectionally** (HTTP 409). Lifecycle state is pruned lazily via each child's `.poll()`; it
is lost on FastAPI restart by design (a restart cannot know about pre-restart OS processes), and
completion is otherwise proven durably.

### Network binding

The dashboard binds **`127.0.0.1` only** — there is no LAN / `0.0.0.0` override.

---

## 7. Persistence and evidence model

### Authoritative storage

| Data | Authoritative location | Notes |
|------|------------------------|-------|
| Benchmark runs (Standard Speed rows) | `bench_llm/data/benchmark_results.db` (**SQLite, primary**) | CSV is a compatibility mirror; migration is idempotent via a metadata flag. |
| Standard Speed history read model | Derived from SQLite via `ResultsStore.get_all()` | Read-only; no benchmark logic. |
| Workflow / V2 Quality runs | `data/v2_runs/<run_id>.json` (atomic write) | Single source of truth for a completed run; loaded by `run_id`. |
| Standard Speed launcher logs | `data/speed_runs/<run_id>.log` | Bounded local log; streamed, never captured into memory. |
| Configuration | `bench_llm/config/settings.json` | Read/written only via `config_loader.py`. |

`bench_llm/src/results.py` implements `ResultsStore`: in-memory list with SQLite primary and
CSV backward-compatibility/migration. Rows carry additive, backward-safe columns (Act 7/8/11B)
that load as `None`/blank on historical databases — never fabricated. Each row records a
deterministic `configuration_fingerprint` and a `classification` of `canonical` vs `incomplete`
(`classify_run_for_result`).

### Distinctions (deliberately preserved)

- **Persistent product data** = SQLite rows + V2 artifacts + config. These are the source of
  truth for results and ranking.
- **Diagnostic artefacts** = run logs under `data/`. Not authoritative; never committed.
- **Legacy / retired storage** = the old JSON `results.json` (left untouched, not read) and the
  retired Prompt/Task/Results products. None are resurrected.

No new SQLite write path is introduced by the ranking layer — it reads existing rows/artifacts
only (§8).

---

## 8. Ranking / aggregation read model — DELIVERED

`bench_llm/src/ranking_read_model.py` is a **pure, read-only** adapter over already-persisted
Standard Speed and V2 Quality evidence. It builds the presentation-facing L0/L1/L2 view the
future Results UI consumes; it performs **no benchmark logic** and **invents no scores**.

- **Composition over reimplementation.** It reuses verified helpers rather than reimplementing
  them: `classify_run_for_result` (L1 identity + eligibility), `load_speed_run_by_id` (canonical
  single-run Speed structure, legacy compatibility), and the validated V2 Quality read-model view
  (`build_read_model`). It never executes a benchmark, loads a model, or touches SQLite/CSV.
- **L0 — model identity:** `(model_version, architecture)`. `model_version` is the stable key;
  Dense vs MoE are distinguished by resolved `architecture` (from `num_experts`; unknown when not
  recorded). Configurations are **children** of the model, keyed by
  `(benchmark_family, configuration_fingerprint)` — never siblings. The two families keep their
  own fingerprint spaces and are never joined by fingerprint equality.
- **L1 — tested configuration / quantisation:** children of L0; each belongs to exactly one
  benchmark family.
- **L2 — run / evidence:** individual runs with validity + provenance. Eligibility (§6 of the
  results-data-contract): a run contributes iff it is a benchmark run, `completed` at the point/run
  level, every required field present and non-null, and its parent configuration is `canonical`.
- **N/A is not zero.** Partial / failed / unsupported / diagnostic evidence never becomes a numeric
  zero; unavailable dimensions render as `None`, distinct from `0`.
- **Composite score remains explicitly unavailable.** No Overall Solo Bench scoring formula exists;
  the aggregation exposes component scores, eligibility, available/missing dimensions and provenance,
  plus a reserved `overall_solo_bench_score` placeholder. A composite score must remain absent until
  its contract is separately approved.
- **No invented families.** Only Speed (Standard Speed) and Agentic (V2 Quality / Workflow) have an
  implemented benchmark + scoring contract; Context-degradation and Intelligence are represented as
  `unavailable`, never inferred or zeroed.

Ownership guards: a persisted Speed run attaches only to its own model identity's buckets, so every
L0 bucket inherits only that model's runs (no cross-model contamination).

**API routes** (`routes/ranking.py`): `GET /api/ranking`, `GET /api/ranking/summary`,
`GET /api/ranking/models`. Error mapping: malformed `run_id` → 400, unprocessable artifact → 422,
internally-inconsistent persisted data → 500; one invalid Workflow artifact is skipped from the
Agentic dimension without aborting the whole surface.

---

## 9. Results / UI architecture

The result experience is split into **API (read models) + static pages**, with no generic
mixed-Results product:

| Surface | Route | Data source | Family |
|---------|-------|-------------|--------|
| History (newest first, point-level) | `/results` → `results.html` | `/api/results` (`routes/results.py`) | Standard Speed |
| Single run detail by point | `/speed/results/{run_id}` → `speed-result.html` | `/api/speed/runs/{run_id}` (`routes/v2_speed.py`) | Standard Speed |
| Single run detail by suite/check | `/v2/results/{run_id}` → `v2-result.html` | `/api/v2/results/{run_id}` (`routes/v2_results.py`) | Workflow |
| Ranking / aggregation grid (future UI) | — | `/api/ranking*` (`routes/ranking.py`) | Speed + Agentic |

All data comes from validated read models; static pages derive `run_id` from the URL and fetch it.
No per-run HTML is generated server-side.

**Planned UI (not built):** Context-degradation results, Intelligence results, multi-model
comparison view, and future composite-score views have no implementation today. The ranking API
exists as a prerequisite for a future Results UI but does not itself render any grid.

---

## 10. Governance and roadmap relationship

Engineering-state governance for contributors:

- **`docs/ROADMAP.md` is the single canonical roadmap.** It uses immutable `RM-26-AA-NNNN`
  identifiers; legacy Acts/slugs are preserved as metadata only, never as machine keys.
- **`NEXT` is derived** from `TODO` items in document order, preferring the first whose
  dependencies are all `DONE`. It is never stored.
- **`ACTIVE` represents explicitly started work** (normally zero or one item).
- **`FUTURE` does not auto-promote** to `TODO`/`ACTIVE`; promotion requires explicit intent.
- **Retired mechanisms:** the legacy Stepstone project-goal backend and `.worklist/worklist.json`
  are retired and are **not** authoritative. Git history is implementation evidence, not the
  roadmap.

---

## 11. Known architectural limitations

These are stated as implemented boundaries, **not** as a security audit or stronger sandboxing
guarantee than the code provides:

- **Generated-code execution is an uncontrolled host-subprocess boundary on Windows** — no
  OS-level network egress block or absolute-filesystem sandbox (see §6). Stated honestly as a
  boundary, not a sandbox.
- **Concurrency guard state is in-memory and lost on FastAPI restart.** Pre-restart OS processes
  are unknown; completion is otherwise proven durably via rows/artifacts.
- **Composite Overall Solo Bench score is unavailable** — no approved scoring formula exists yet.
- **Windows-focused**; untested on macOS/Linux.
- **LM Studio-only backend** — other providers are not supported.
- **Configuration-dependent numbers** — results depend on the specific model, quantization,
  hardware, and LM Studio settings; cross-config comparison requires care.

---

## 12. Planned extension points

These map to roadmap items, not to current implementation:

- **Context benchmark family (RM-26-AA-0009)** — DELIVERED: runner (`src/v2_context_suite_runner.py`),
  deterministic corpus (`src/context_corpus/`), atomic artifacts (`src/v2_context_artifact.py`), read
  model (`src/v2_context_read_model.py`) and API (`src/routes/context.py`). Only the degradation Results
  UI surface remains (RM-26-AA-0018).
- **AI Intelligence benchmark family (RM-26-AA-0010)** — discriminative capability benchmark for
  cases where Workflow saturates; requires corpus, scoring methodology, and UI before any
  implementation.
- **Multi-model comparison view (RM-26-AA-0013)** — side-by-side degradation across multiple models
  at identical context points, built on the Context family.
- **Composite scoring** — an approved Overall Solo Bench formula would slot into the reserved
  `overall_solo_bench_score` placeholder in the ranking read model without schema churn.

---

## Appendix — authoritative implementation map

| Area | Authoritative files | Status |
|------|---------------------|--------|
| Application / FastAPI app | `src/main.py`, `src/server_launcher.py`, `src/__main__.py` | DELIVERED |
| Route modules | `src/routes/{config,models,results,v2_results,v2_workflow,v2_speed,context,ranking}.py` | DELIVERED |
| Static UI / pages | `bench_llm/static/*.{html,js,css}` | DELIVERED |
| Configuration boundary | `src/config_loader.py`, `config/settings.json` | DELIVERED |
| Model lifecycle / LM Studio | `src/model_lifecycle.py`, `src/benchmark.py` (`fetch_models`) | DELIVERED |
| Standard Speed family | `src/v2_speed_suite_runner.py`, `src/routes/v2_speed.py`, `src/routes/results.py` | DELIVERED |
| Workflow family | `src/v2_workflow_runner.py`, `src/v2_quality_suite_runner.py`, `src/routes/v2_workflow.py`, `src/routes/v2_results.py` | DELIVERED |
| Context family | `src/context_corpus/`, `src/v2_context_suite_runner.py`, `src/v2_context_artifact.py`, `src/v2_context_read_model.py`, `src/routes/context.py` | DELIVERED |
| AI Intelligence family | — (roadmap only) | RESEARCH |
| Execution boundary | `src/execution_boundary.py` | DELIVERED |
| Backend URL allow-list | `src/backend_url_policy.py` | DELIVERED |
| Concurrency guard | `src/standard_run_guard.py` | DELIVERED |
| Persistence (runs) | `src/results.py`, `data/benchmark_results.db` (+ CSV mirror) | DELIVERED |
| Persistence (Workflow artifacts) | `src/v2_quality_artifact.py`, `data/v2_runs/*.json` | DELIVERED |
| Ranking read model | `src/ranking_read_model.py`, `src/routes/ranking.py` | DELIVERED |
| Testing (pytest) | `bench_llm/pytest.ini`, `bench_llm/tests/`, `bench_llm/tasks/` | DELIVERED |
| Roadmap governance | `docs/ROADMAP.md` | DELIVERED |
