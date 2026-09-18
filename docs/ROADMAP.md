# Solo Dev LLM Bench — Roadmap

Focused local-LLM benchmark with four benchmark families: **Standard Speed**, **Workflow**, **Context**, and **AI Intelligence**. This document is the single canonical project roadmap.

Stored lifecycle states are `DONE`, `ACTIVE`, `TODO`, `FUTURE`. `NEXT` is derived from `TODO` in document order and is never stored. Identifiers `RM-26-AA-NNNN` are immutable identities only; they do not encode status, priority, hierarchy, or execution order. Legacy Acts/slugs are preserved as metadata, not as canonical keys.

## ACTIVE

## TODO

### RM-26-AA-0017 — Speed & Workflow Results experience

Integrate the two delivered benchmark families into the unified Results foundation as one cohesive results experience (not disconnected pages).

Standard Speed telemetry exposed where available: TTFT, prompt/prefill processing throughput, generation/decode throughput, cold + warm repeatability runs, per-run/config evidence, config/quantization transparency, unsupported/gap points (never converted to zero), and validity/failure state — drill-down rather than only headline metrics. Prompt/prefill processing is a visible dimension of the Speed results experience, not a separate benchmark.

Workflow presented against its authoritative backend/read-model output: overall Workflow result/score, suite-level breakdown, test/case/request evidence where available, failure-first presentation with collapsible successful evidence, filters, model/config transparency, validity/failure state, and deep-link/run evidence where appropriate. The frontend does not independently recompute Workflow scoring; Workflow remains a deterministic correctness benchmark, never an LLM-judge quality measure.

- Category: product
- Depends on: RM-26-AA-0016, RM-26-AA-0015
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: speed-workflow-results-experience

### RM-26-AA-0018 — Context degradation Results UI

Results surface for the Context benchmark family's degradation signal. Distinct from, and downstream of, RM-26-AA-0009 (the benchmark must exist before this UI can consume authoritative results). Scope: context-size progression, degradation/drift curve, baseline/reference point, unsupported/gap states, validity/failure states, individual run/evidence drill-down, and model/config transparency. Consumes the Context read-model output through the unified Results foundation; does not define or hard-code benchmark design (e.g. target context points) here — that is owned by RM-26-AA-0009. N/A/unsupported never coerced to zero.

- Category: product
- Depends on: RM-26-AA-0016, RM-26-AA-0009
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: context-degradation-results-ui

## FUTURE

### RM-26-AA-0010 — AI Intelligence benchmark family

Genuinely discriminative model-capability benchmark for cases where Workflow tasks saturate and materially different models achieve similar deterministic scores. Tests reasoning, synthesis, planning, judgement, problem solving, instruction interpretation, ambiguity handling, and multi-step decision quality. Design goals: avoid easy/saturated tasks, separate materially different capability levels, avoid simply duplicating Workflow, never present one arbitrary number as universal intelligence, and define scoring methodology before implementation. Not implemented; research item until a benchmark contract is agreed.

- Category: product / research
- Depends on: RM-26-AA-0002, RM-26-AA-0009
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: ai-intelligence-benchmark-family

### RM-26-AA-0012 — Streaming research

Investigate streaming token-by-token results and live metrics (TTFT / throughput as tokens arrive) for the dashboard, evaluating a scripted headless render against LM Studio versus the current non-streaming flow. Independent exploration; no hard dependency on earlier engineering buckets.

- Category: research
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: streaming-research

### RM-26-AA-0013 — Multi-model comparison view

Side-by-side comparison of exactly two selectable model/configurations across every available benchmark dimension (Standard Speed, Workflow/Agentic, Context once delivered, Intelligence once delivered), plus config/quantization transparency. Compares only dimensions that are valid for each subject; missing / unsupported / N/A is preserved as a gap and never coerced to zero. Shows/hides unsuccessful runs where useful, keeps validity/failure state visible, and preserves evidence drill-down (L0→L1→L2) into the underlying run. Ships incrementally: supports currently delivered dimensions first and extends once Context and Intelligence are delivered — it does not depend on AI Intelligence to ship Speed/Workflow/Context comparison. Includes exportable scorecards (Markdown, image/JPEG, PDF). The unified Results UI foundation underpins this surface; frontend performs no scoring or dimension recomputation.

- Category: product / research
- Depends on: RM-26-AA-0016
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: multi-model-comparison-view; scorecard-export

### RM-26-AA-0019 — Composite scoring contract design

Research/design item that decides whether an Overall Solo Bench composite score should exist at all, and if so under what approved methodology. Defines (and only then approves) any weighting/formula; guarantees N/A/unsupported is never coerced to zero; requires explicit human approval before any composite value exists. Explicitly blocks any Overall/composite Results UI: the current ranking read model provides no overall score (`overall_solo_bench_score` reserved placeholder stays null), and this contract must be approved before RM-26-AA-0020 ships.

- Category: product / research
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: composite-scoring-contract-design

### RM-26-AA-0020 — Overall / composite Results view

Future presentation shell for an Overall/composite results area. Blocked on an approved composite scoring contract (RM-26-AA-0019); it must not invent a composite score, average existing benchmark values, treat N/A as 0, or define arbitrary weights. Distinguishes "Overall UI shell / future presentation" from the "approved composite scoring contract": this item builds only the presentation surface once a contract exists, consuming component scores from the ranking read model without recomputation.

- Category: product
- Depends on: RM-26-AA-0019
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: overall-composite-results-view

### RM-26-AA-0021 — Intelligence Results integration

Results UI for the AI Intelligence benchmark family. Purely downstream: cannot be implemented until its benchmark contract exists (RM-26-AA-0010). Represents only the results-integration work and its dependency; does not design the Intelligence corpus, tasks, or scoring here. Consumes authoritative Intelligence output through the unified Results foundation once delivered.

- Category: product / research
- Depends on: RM-26-AA-0010, RM-26-AA-0016
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: intelligence-results-integration

## DONE

### RM-26-AA-0016 — Unified Results UI foundation

Delivered. A unified Results experience rather than independent disconnected result pages. Provides the shared shell consumed by every benchmark-family results surface: unified Results navigation with benchmark-specific tabs/views, shared cards/modules and collapsible sections, L0→L1→L2 drill-down conventions (model family/version → configuration/quantization → individual run evidence), and a single loading/error/N/A/invalid-state contract. Consumes authoritative backend/read models only (`build_ranking`, `load_speed_run_by_id`, `load_v2_result`); the frontend performs no benchmark scoring or dimension recomputation, and N/A is never treated as zero. Applies the agreed dark visual system (dark mode or nothing — no light/dark toggle). Extensible with slots for later benchmark families (Context, Intelligence) without restructuring the shell.

- Category: product
- Depends on: RM-26-AA-0015, RM-26-AA-0008
- Updated: 2026-09-18T16:36:40Z
- Legacy ID: unified-results-ui-foundation

#### Evidence

- `bench_llm/static/results.html` — unified Results shell page (shared nav with benchmark-specific tabs/views; Overall / Speed / Workflow tabs; Context & Intelligence placeholders)
- `bench_llm/static/results-shell.js` — shared shell: tab navigation, L0→L1→L2 model drill-down via native `<details>/<summary>`, loading/error/N/A fragments; formats and drills down only from `/api/ranking`, performs no client-side scoring or composite
- `bench_llm/static/results-shell.css` — dark visual system ("dark mode or nothing", no light/dark toggle), shared cards/modules, collapsible-section styling, explicit `:focus-visible` on disclosure controls and tabs
- `bench_llm/src/ranking_read_model.py` + `bench_llm/src/routes/ranking.py` — authoritative read-only L0/L1/L2 projection; composite stays explicitly unavailable; N/A distinct from 0
- `bench_llm/tests/test_results_shell_route.py` — Results-shell route tests (dark theme, five areas, composite unavailable, unimplemented dimensions unavailable-with-reason-not-scored, shell formats-only, collapsible L0/L1 primitive present, distinct state fragments)
- `docs/architecture.md` §9 updated to reflect the delivered unified Results shell
- browser acceptance: `/results` renders; tab navigation verified, dark system (shell bg rgb(7,17,31), text rgb(232,238,246)), collapsible L0→L1→L2 present, no console errors during interaction
- measured: fast gate 603 passed / 40 deselected; targeted routes 74 passed; full suite 643 passed (all exit 0)

### RM-26-AA-0011 — Test architecture and fast gate

Establish a clean three-tier test taxonomy plus a FAST local gate so refactors can be validated quickly without the multi-minute full-suite cost. Fast gate = offline/deterministic tests only (`python test_gate.py`, ~56s, 587 tests); full suite unchanged as the authoritative regression gate (`python test_gate.py full`, 627 tests). Markers `slow`/`integration`/`live` tag the deliberately heavy/toolchain-dependent/live tiers; the fast gate excludes them via `-m "not slow and not integration and not live"`. Exit-code discipline enforced through a subprocess wrapper with no masking (see `tests/test_test_gate.py`). Full suite: 627 passed.

- Category: testing
- Depends on: RM-26-AA-0005
- Updated: 2026-09-19T09:00:00Z
- Legacy ID: test-architecture-and-fast-gate; D4

#### Evidence

- `bench_llm/test_gate.py` — canonical tiered entry point (`fast` default, `full`, `targeted <family>`); runs pytest as a subprocess and propagates the real exit code verbatim (no false greens)
- `bench_llm/pytest.ini` — registered markers `slow`, `integration`, `live`
- `tests/test_test_gate.py` — exit-code discipline tests (passing→0, failing→non-zero, empty selection→non-zero, unknown family→visible error)
- `tests/test_v2_quality_store.py`, `tests/test_v2_quality_executor.py` tagged `slow`; `tests/test_java_execution_isolation.py` tagged `integration`
- `docs/architecture.md` §Testing rewritten with the tiered taxonomy + canonical commands
- `README.md` Development/testing section updated with fast/full commands
- measured: full suite 627 passed in ~253s; fast gate 587 passed in ~56s (~4.6x faster); no regressions

### RM-26-AA-0009 — Context benchmark family

Delivered as a deterministic, evidence-preserving benchmark measuring how model capability changes as usable context grows at fixed points 15K/30K/60K/120K/180K/240K where supported. Quality retention (exact-match fact recall) and performance degradation (TTFT/prefill/degradation growth, context-capacity limits). Unsupported points persist as null-scored gaps and are never rescaled to zero. Distinct from Standard Speed, prompt/prefill throughput, Workflow/Agentic, AI Intelligence, composite scoring, and the Results UI (RM-26-AA-0018).

- Category: product
- Depends on: RM-26-AA-0003
- Updated: 2026-09-18T14:00:00Z
- Legacy ID: context-benchmark-family

#### Evidence

- `bench_llm/src/context_corpus/` — deterministic corpus (builder + scoring): fixed known facts, cache-busting per-run filler, exact-match scoring with a None-not-zero contract and divide-by-zero guard
- `bench_llm/src/v2_context_suite_runner.py` — execution plumbing only; never imports the legacy executor
- `bench_llm/src/v2_context_artifact.py` — atomic schema-versioned artifacts; one row per supported point; unsupported points persisted as null-scored gaps with an explicit reason
- `bench_llm/src/v2_context_read_model.py` — authoritative projection with baseline/degradation over authoritative scores and ownership-fingerprint integrity checks (no cross-model evidence leakage)
- `bench_llm/src/routes/context.py` + temporary shell in `bench_llm/src/main.py` — launch/status/read API; the full degradation Results UI is RM-26-AA-0018
- `docs/architecture.md` §5.3 updated to DELIVERED
- tests: test_context_corpus.py, test_context_suite_runner.py, test_context_read_model.py, test_context_route.py (full suite 623 passed)

### RM-26-AA-0008 — Author architecture documentation

Concise architecture document covering: launcher, four benchmark families, active routes, LM Studio lifecycle, heavyweight-run guard, persistence, read models, result pages, test organisation. Conceptual layout preserved in this roadmap.

- Category: documentation
- Depends on: RM-26-AA-0007
- Updated: 2026-09-18T13:32:08Z
- Legacy ID: author-architecture-documentation; D3

#### Evidence

- file `docs/architecture.md` — Solo Dev BenchLLM Architecture (implementation-grounded)
- all 19 route decorators verified against source; cited read-model symbols resolve
- DELIVERED/PLANNED/RESEARCH labels cross-checked vs README and current source
- covers launcher, four families, active routes, LM Studio lifecycle, concurrency guard, persistence, read models, result pages, test organisation

### RM-26-AA-0007 — Rewrite README

Rewrite (not patch) the README to describe the current application: four benchmark families, delivered vs planned, LM Studio requirements, model lifecycle, Standard Speed + Workflow methodology, result pages, limitations. Key wording: Workflow measures deterministic developer-task correctness; Solo Dev LLM Bench is **not** a universal intelligence leaderboard; the future AI Intelligence suite is separate from Workflow.

- Category: documentation
- Depends on: RM-26-AA-0003
- Updated: 2026-09-17T22:42:15Z
- Legacy ID: rewrite-readme; D1

#### Evidence

- commit `6c79c2d` — docs: rewrite README for current benchmark architecture
- README unchanged since commit (no regression to HEAD)

### RM-26-AA-0001 — Standard Speed benchmark family

Delivered. Measures practical local-model execution performance at fixed context points (8K/16K/32K): TTFT, full-prefill + generation throughput, actual input/output tokens, target-vs-actual context error, wall time; calibrated token sizing, cache-busted prefill, warm generation samples, metric versioning v3. Supporting features: model discovery, model load/unload lifecycle, shared heavyweight-run guard, dedicated Standard Speed result page, Standard Speed history grouped by run, 8K→16K→32K point-level summaries, legacy metric warnings, calibrated context targets within tolerance.

- Category: product
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: standard-speed-benchmark-family; Acts 17, 17.1, 18, 19, 19.1, 20, 22, 23

#### Evidence

- docs/ROADMAP.md status DELIVERED
- Canonical provenance corpus, runner+execution contract, live executor/extractors/prompts, authoritative artifacts

### RM-26-AA-0002 — Workflow benchmark family

Delivered. Deterministic developer-workflow correctness/reliability benchmark: 166 checks, 5 suites, no LLM judge, no subjective scoring, authoritative V2 runner, separate suite execution, durable result artifacts, dedicated Workflow result page. NOT intended as a universal model-intelligence leaderboard.

- Category: product
- Depends on: RM-26-AA-0001
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: workflow-benchmark-family; Acts 8, 9, 10, 11, 12 (v2 results UI), Act 16 (workflow suite launch)

#### Evidence

- docs/ROADMAP.md status DELIVERED

### RM-26-AA-0003 — Retire legacy Prompt and Task product (M1)

Retired the obsolete custom Prompt / Task Manager product. Scope: orphaned Prompt Manager frontend, orphaned Task Manager frontend, prompt CRUD APIs, obsolete Task CRUD APIs, dead task-management persistence/helpers, associated tests, stale CSS/DOM references. Did not remove active result-history until its dependencies were separately retired.

- Category: maintenance
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: retire-legacy-prompt-and-task-product; M1

#### Evidence

- commit `4d79064` — refactor: retire legacy Prompt and Task product
- 591/591 tests passed; legacy Results compatibility preserved

### RM-26-AA-0004 — Retire legacy Results product (M2)

Retired the legacy Results product. Simplified `/results` into the current history view: `/results` = Standard Speed history; `/speed/results/{run_id}` = single Standard Speed run; `/v2/results/{run_id}` = single Workflow run. Retirement candidates addressed; historical runtime data preserved. Did not remove active result-history until its dependencies (M1) were separately retired.

- Category: maintenance
- Depends on: RM-26-AA-0003
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: retire-legacy-results-product; M2

#### Evidence

- commit `53a4583` — refactor: retire legacy Results product
- 535/535 tests passed

### RM-26-AA-0005 — Remove production-dead leftovers (M3)

After legacy retirement, re-ran reachability analysis and removed production-dead modules, stale imports/CSS, obsolete compatibility helpers, forbidden/superseded executor entry points, dead source-string tests, and obsolete historical product comments. `benchmark_v2_quality.py` flagged for re-evaluation. Did not refactor large active modules before dead code was removed. Protected runtime data (`benchmark_results.csv`, `benchmark_results.db`) byte-for-byte unchanged; Standard Speed + Workflow products unaffected.

- Category: maintenance
- Depends on: RM-26-AA-0004
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: remove-production-dead-leftovers; M3

#### Evidence

- commit `075ece1` — refactor: remove production-dead leftovers
- 524/524 tests passed against isolated temp storage

### RM-26-AA-0006 — Maintain the roadmap document

Reconciled this canonical ROADMAP.md against implementation and the project-goal mirror: verified DONE/ACTIVE/TODO/FUTURE state, preserved legacy Acts as metadata (not one item per commit), confirmed `NEXT` derivation, and closed out standing maintenance. Git history is implementation evidence, not the roadmap itself.

- Category: documentation
- Updated: 2026-09-16T00:00:00Z
- Legacy ID: maintain-the-roadmap-document; D2

#### Evidence

- docs/ROADMAP.md status DONE (reconciled)
- reconcile against implementation completed; `RM-26-AA-0014` verified and recorded

### RM-26-AA-0014 — Speed benchmark repeatability contract

Delivered. Standard Speed now executes one independent run per stage for every canonical point: 1 cold (cache-busted full-prefill) plus 2 warm (`warm_a` / `warm_b`). Each run is persisted independently so cold and warm are never averaged together; prefill throughput stays a cold-only signal and generation is a warm-only mean. A stage-aware read model relaxes the one-row-per-point guard to accept cold + two warm, preserves explicit per-stage provenance (stage tag, status, telemetry) for successful runs by default with unsuccessful ones behind an explicit toggle, keeps legacy single-row evidence backwards-compatible, rejects duplicate-stage rows instead of collapsing them, and captures optional reasoning-token metrics only when the backend reports them.

- Category: product
- Updated: 2026-09-16T00:00:00Z
- Legacy ID: speed-benchmark-repeatability-contract

#### Evidence

- design artifact: `docs/speed-repeatability-contract.md`
- independent `cold` / `warm_a` / `warm_b` runs persisted independently (`bench_llm/src/v2_speed_suite_runner.py`)
- stage-aware read model + per-stage provenance (`bench_llm/src/v2_speed_read_model.py`)
- warm-only generation aggregation; prefill kept cold-only
- legacy single-row compatibility preserved
- duplicate-stage evidence rejected rather than collapsed
- optional reasoning-token capture only when reported
- full suite: 558 passed
- focused repeatability suite: 94 passed

### RM-26-AA-0015 — Canonical ranking / aggregation read model (Results UI prerequisite)

Delivered the backend/read-model layer that projects already-persisted Standard Speed and V2 Quality (Agentic) evidence into the canonical L0/L1/L2 representation the future Results UI consumes. Purely read-only: it reuses verified existing readers (:func:`load_speed_run_by_id`, :func:`classify_run_for_result`, :func:`src.v2_quality_read_model.build_read_model`) by composition, performs no benchmark logic, rewrites no historical rows, and implements NO composite score.

- Category: product
- Updated: 2026-09-16T00:00:00Z
- Legacy ID: canonical-ranking-read-model; results-aggregation-backend

#### Evidence

- design artifacts: `docs/results-data-contract.md`
- read-only aggregation layer: `bench_llm/src/ranking_read_model.py`
- read-only HTTP adapter registered in `bench_llm/src/main.py`: GET `/api/ranking`, GET `/api/ranking/summary`, GET `/api/ranking/models` (HTTP 200 verified)
- identity rules: L0 = `(model_version, architecture)` with dense / moe / unknown resolved from `num_experts`; configurations are children keyed by `(benchmark_family, configuration_fingerprint)`; per-run provenance + Run IDs preserved
- eligibility (§6): completed + canonical + all required fields present contribute; partial/failed/interrupted/unsupported evidence never coerces to numeric zero
- **composite Overall Solo Bench score remains explicitly `unavailable`** (no weights/formula invented); reserved placeholder is null
- N/A rendered as `None` (`score: None`, `status: unavailable`) for unimplemented dimensions -- verified distinct from `0`
- speed repeatability contract preserved and never collapsed (cold full-prefill vs warm-only generation kept separate)
- integrity-skip behaviour: one invalid Workflow artifact is excluded from the Agentic dimension without aborting the whole ranking surface
- test evidence: ranking read-model tests 12/12, route tests 8/8; focused speed/results/v2-quality regression 219 passed; **full suite 578 passed**

Not delivered by this item (deferred): visual Results UI, overall Solo Bench weighting/formula, model-vs-model / scorecard UI, Markdown/JPEG/PDF export, Agentic context-degradation scoring, Intelligence benchmark/scoring.

