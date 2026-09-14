# Solo Dev LLM Bench — Roadmap

Solo Dev LLM Bench is evolving into a focused local-LLM benchmark with four benchmark families:

1. Standard Speed
2. Workflow
3. Context
4. AI Intelligence

The project deliberately separates performance, deterministic workflow reliability, long-context behaviour, and general model capability rather than collapsing them into one score.

---

# Current Product State

## 1. Standard Speed

**Status: DELIVERED**

Purpose:

Measure practical local-model execution performance at fixed context sizes.

Current standard points:

- 8K
- 16K
- 32K

Current metrics:

- TTFT
- full-prefill throughput
- generation throughput
- actual input tokens
- target-vs-actual context error
- output tokens
- wall time

Current methodology:

- calibrated runtime token sizing
- cache-busted full-prefill measurement
- warm generation samples
- metric-versioned persistence
- current metric version: v3

Delivered supporting features:

- model discovery
- model load/unload lifecycle
- shared heavyweight-run guard
- dedicated Standard Speed result page
- Standard Speed history grouped by run
- 8K → 16K → 32K point-level summaries
- legacy metric warnings
- calibrated context targets within tolerance

---

## 2. Workflow

**Status: DELIVERED**

Purpose:

Measure whether a model can reliably perform deterministic developer-workflow tasks.

Current contract:

- 166 deterministic checks
- 5 suites
- no LLM judge
- no subjective scoring
- authoritative V2 runner
- separate suite execution
- durable result artifacts
- dedicated Workflow result page

Workflow is a task-correctness and reliability benchmark.

It is **not** intended to be a universal model-intelligence benchmark.

---

# Planned Benchmark Families

## 3. Context

**Status: PLANNED**

Purpose:

Measure how model quality and performance change as usable context grows.

Planned standard points:

- 15K
- 30K
- 60K
- 120K
- 180K
- 240K where supported

Planned measurements:

- quality retention
- workflow degradation
- TTFT growth
- prefill degradation
- generation degradation
- context-capacity limits
- unsupported-point handling

Unsupported context points should be represented as unsupported/gaps.

Scores must not be rescaled to hide unsupported context sizes.

Future comparison view:

- multiple models
- identical context points
- side-by-side degradation curves

Planned multi-model comparison view (tracked as goal `multi-model-comparison-view`):

**Status: PLANNED**

Compares several models at identical context points via side-by-side degradation curves.

---

## 4. AI Intelligence

**Status: PLANNED / RESEARCH**

**Not implemented in the current application.**

Purpose:

Provide a genuinely discriminative model-capability benchmark for cases where Workflow tasks saturate and materially different models achieve similar deterministic scores.

This suite should test harder capabilities such as:

- reasoning
- synthesis
- planning
- judgement
- problem solving
- instruction interpretation
- ambiguity handling
- multi-step decision quality

Design goals:

- avoid easy/saturated tasks
- separate materially different model capability levels
- avoid simply duplicating Workflow
- avoid presenting one arbitrary number as universal intelligence
- define scoring methodology before implementation

Current state:

- no runner
- no API
- no persistence schema
- no UI card
- no production tests

This remains a research item until a benchmark contract is agreed.

---

# Maintenance Roadmap

The current priority is to finish removing the legacy product before adding another major benchmark suite.

## M1 — Retire Prompt / Task Manager Product

**Status: NEXT**

Remove the obsolete custom Prompt / Task Manager product.

Scope:

- orphaned Prompt Manager frontend
- orphaned Task Manager frontend
- prompt CRUD APIs
- obsolete Task CRUD APIs
- dead task-management persistence/helpers
- associated tests
- stale CSS and DOM references

Do not remove active result-history functionality until its dependencies are separately retired.

## M2 — Retire Legacy Results Product

**Status: PLANNED**

Simplify `/results` into the current product's history view.

Target:

`/results`
- Standard Speed history

`/speed/results/{run_id}`
- single Standard Speed run

`/v2/results/{run_id}`
- single Workflow run

Retirement candidates:

- legacy task-history UI
- generic/custom benchmark rows
- generic result charts
- old comparison views
- obsolete filtering/navigation
- APIs used only by retired result surfaces
- obsolete result tests

Historical runtime data may remain archived.

Removing runtime interpretation code does not require deleting historical data.

## M3 — Remove Production-Dead Leftovers

**Status: PLANNED**

After legacy product retirement, re-run reachability analysis.

Candidates include:

- production-dead modules
- stale imports
- stale CSS
- obsolete compatibility helpers
- forbidden/superseded executor entry points
- dead source-string tests
- obsolete historical product comments

Known candidate for re-evaluation:

- `benchmark_v2_quality.py`

Do not refactor large active modules before dead code is removed.

---

# Documentation Roadmap

## D1 — Rewrite README

**Status: PLANNED**

The existing README describes an older version of the application and should be rewritten rather than patched incrementally.

New README should cover:

1. What Solo Dev LLM Bench is
2. Four benchmark families
3. Delivered vs planned functionality
4. LM Studio requirements
5. Model lifecycle
6. Standard Speed methodology
7. Workflow methodology
8. Result pages
9. Limitations
10. Links to architecture and roadmap documents

Important wording:

Workflow measures deterministic developer-task correctness.

Solo Dev LLM Bench is **not a universal intelligence leaderboard**.

The future AI Intelligence suite is separate from Workflow.

## D2 — Maintain This ROADMAP.md

**Status: PLANNED**

This document should be the canonical future-work roadmap.

Git commit history remains implementation evidence, not the roadmap itself.

Historical Acts should not be recreated as one roadmap item per commit.

## D3 — Architecture Documentation

**Status: PLANNED**

Create a concise architecture document covering:

- launcher
- four benchmark families
- active routes
- LM Studio lifecycle
- heavyweight-run guard
- persistence
- read models
- result pages
- test organisation

Target conceptual architecture:

```text
Solo Dev LLM Bench
│
├── Standard Speed       DELIVERED
├── Workflow             DELIVERED
├── Context              PLANNED
└── AI Intelligence      PLANNED / RESEARCH
```

## D4 — Test Architecture & Fast Gate

**Status: PLANNED**

Document the test architecture across the four benchmark families and establish a fast regression gate so the suite can run frequently without waiting on slow or LLM-judge tests. Tracked as goal `test-architecture-and-fast-gate`.
