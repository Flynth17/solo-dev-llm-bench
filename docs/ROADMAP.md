# Solo Dev LLM Bench — Roadmap

Focused local-LLM benchmark with four benchmark families: **Standard Speed**, **Workflow**, **Context**, and **AI Intelligence**. This document is the single canonical project roadmap.

Stored lifecycle states are `DONE`, `ACTIVE`, `TODO`, `FUTURE`. `NEXT` is derived from `TODO` in document order and is never stored. Identifiers `RM-26-AA-NNNN` are immutable identities only; they do not encode status, priority, hierarchy, or execution order. Legacy Acts/slugs are preserved as metadata, not as canonical keys.

## ACTIVE

## TODO

### RM-26-AA-0009 — Context benchmark family

Measure how model quality and performance change as usable context grows. Planned points 15K/30K/60K/120K/180K/240K where supported: quality retention, workflow degradation, TTFT/prefill/degradation growth, context-capacity limits, unsupported-point handling (shown as gaps; scores must not be rescaled to hide unsupported sizes).

- Category: product
- Depends on: RM-26-AA-0003
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: context-benchmark-family

### RM-26-AA-0011 — Test architecture and fast gate

Establish a clean test architecture plus a FAST local test gate (unit/source-contract tests GPU-free and in-memory; ordered fast gate) so refactors across retirement can be validated quickly. Complements dead-code cleanup (M3); does not require production modules to be restructured first.

- Category: testing
- Depends on: RM-26-AA-0005
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: test-architecture-and-fast-gate; D4

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

Side-by-side degradation / comparison across MULTIPLE models at IDENTICAL context points (15K/30K/60K/120K/180K/240K where supported); unsupported points shown as gaps, scores not rescaled. Builds on the Context benchmark family before AI Intelligence research.

- Category: product / research
- Depends on: RM-26-AA-0009
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: multi-model-comparison-view

## DONE

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

