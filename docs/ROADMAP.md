# Solo Dev LLM Bench — Roadmap

Focused local-LLM benchmark with four benchmark families: **Standard Speed**, **Workflow**, **Context**, and **AI Intelligence**. This document is the single canonical project roadmap.

Stored lifecycle states are `DONE`, `ACTIVE`, `TODO`, `FUTURE`. `NEXT` is derived from `TODO` in document order and is never stored. Identifiers `RM-26-AA-NNNN` are immutable identities only; they do not encode status, priority, hierarchy, or execution order. Legacy Acts/slugs are preserved as metadata, not as canonical keys.

## ACTIVE

### RM-26-AA-0006 — Maintain the roadmap document

Standing maintenance of this canonical ROADMAP.md: keep structure valid, reconcile state against implementation, and preserve legacy Acts as metadata (not one item per commit). Git history is implementation evidence, not the roadmap itself.

- Category: documentation
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: maintain-the-roadmap-document; D2

## TODO

### RM-26-AA-0007 — Rewrite README

Rewrite (not patch) the README to describe the current application: four benchmark families, delivered vs planned, LM Studio requirements, model lifecycle, Standard Speed + Workflow methodology, result pages, limitations. Key wording: Workflow measures deterministic developer-task correctness; Solo Dev LLM Bench is **not** a universal intelligence leaderboard; the future AI Intelligence suite is separate from Workflow.

- Category: documentation
- Depends on: RM-26-AA-0003
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: rewrite-readme; D1

### RM-26-AA-0008 — Author architecture documentation

Concise architecture document covering: launcher, four benchmark families, active routes, LM Studio lifecycle, heavyweight-run guard, persistence, read models, result pages, test organisation. Conceptual layout preserved in this roadmap.

- Category: documentation
- Depends on: RM-26-AA-0007
- Updated: 2026-09-14T20:00:00Z
- Legacy ID: author-architecture-documentation; D3

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
