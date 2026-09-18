# Solo Dev BenchLLM

**Solo Dev BenchLLM measures how a locally hosted LLM performs when you change the model, hardware, or execution environment.**

It measures **performance and deterministic correctness — not intelligence, quality rankings, or model recommendations.**

Repository identity: `solo-dev-llm-bench` · Branch: `feat/benchmark-v2`

---

## What it measures

Practical, repeatable questions for a solo developer running models locally through LM Studio:

- If I swap to a different model, how much slower or faster will it be?
- What happens to **TTFT** (time to first token)?
- What happens to sustained **tokens/sec**?
- How do cold and warm runs differ?
- Is the experience still usable on a laptop vs. a desktop?

This is **not** a model-quality evaluator, a cloud service, or a universal intelligence leaderboard. You make the decision; this tool reports measurements.

---

## Current status / benchmark families

| Family | Status | What it does |
|--------|--------|--------------|
| **Standard Speed** | ✅ DELIVERED | Local-model execution performance at fixed context points (8K / 16K / 32K): TTFT, full-prefill throughput, generation throughput. |
| **Workflow** | ✅ DELIVERED | Deterministic developer-workflow correctness/reliability — 166 checks across 5 suites, no LLM judge. Not a general-intelligence score. |
| **Context** | 🧩 PLANNED (not implemented) | How quality and performance degrade as usable context grows (up to ~240K where supported). |
| **AI Intelligence** | 🔬 RESEARCH / PLANNED (not implemented) | A genuinely discriminative capability benchmark for cases where Workflow saturates. Research item only — no design finalized. |

Never treat a *planned* family as available functionality.

---

## Standard Speed

Measures how fast a loaded model runs on **fixed input-context pressure**.

**Verified contract (current):**

- Fixed context points: **8K / 16K / 32K** (8192 / 16384 / 32768 tokens).
- Reported metrics per point:
  - **TTFT** — time to first token.
  - **Full-prefill throughput** — prompt processed per second at the real context size.
  - **Generation throughput** — sustained tokens/sec during decode.
- **Calibrated runtime token sizing** — prompts are sized so the *actual* input lands near the canonical target, not merely labeled with it. A cache-busting deterministic prefix prevents KV-cache reuse from faking a fast prefill rate.
- **Authoritative metric version: `v3`** (persisted per run) so corrected full-prefill numbers are distinguishable from legacy cached-TTFT artifacts.
- Persistent run history, grouped by run, with 8K → 16K → 32K point-level summaries.

**Result surfaces:**

| URL | Shows |
|-----|-------|
| `/results` | Standard Speed **history only** — normalized, point-level runs (newest first). Generic/legacy benchmark rows are excluded from this surface. |
| `/speed/results/{run_id}` | Single Standard Speed **detail**, by run and context point. |

Standard Speed measures performance, never correctness or intelligence.

---

## Workflow

A **deterministic** developer-workflow correctness/reliability benchmark. There is no subjective scoring and **no LLM-as-judge** — validation uses deterministic detectors (including Java compilation).

**Verified contract (current):**

- **5 suites:** `python`, `java`, `markdown`, `evidence`, `drift`.
- **166 checks total**, against fixed immutable denominators: python 58, java 52, markdown 20, evidence 30, drift 6.
- Each suite runs in its **own standalone OS process** (no benchmark state leaks between suites); a shared config fingerprint keeps identity identical across suites and runs.
- A partial validation can never *remove* checks from the denominator — e.g. `markdown 19/20` stays `19/20`, never collapses to a meaningless `0/0`.
- **No LLM judge / no subjective scoring.**

**Not intended as a general model-intelligence ranking.** Workflow measures deterministic development/workflow behaviour; strong models can saturate it. That gap is what the planned AI Intelligence family targets (see below).

**Result surface:**

| URL | Shows |
|-----|-------|
| `/v2/results/{run_id}` | Single Workflow **detail**, by suite and check, with pass/total against the fixed denominators. |

---

## Context — PLANNED · not implemented

> ⚠️ **Planned only.** No runner, API, persistence, or result UI exists today. Do not rely on it.

Roadmap intent: measure how model quality and performance change as usable context grows, at points **15K / 30K / 60K / 120K / 180K / 240K where supported**. The focus is **quality retention and performance degradation** as context increases (TTFT/prefill/degradation growth, context-capacity limits).

Unsupported context points would be shown as gaps — scores are never rescaled to hide them. This family underpins a future multi-model side-by-side comparison view.

---

## AI Intelligence — RESEARCH / PLANNED · not implemented

> ⚠️ **Research item only.** Nothing is implemented: no runner, corpus, API, persistence, UI, or scoring design exists and none is finalized.

Intended distinction from Workflow: a genuinely **discriminative capability** benchmark for cases where Workflow tasks saturate and materially different models score too similarly. It would target reasoning, synthesis, planning, judgement, problem-solving, instruction interpretation, ambiguity handling, and multi-step decision quality — explicitly *not* a duplicate of Workflow, and never reduced to a single "universal intelligence" number.

---

## Requirements

- **Operating system:** Windows 10/11 (primary platform; not tested on macOS/Linux).
- **Python:** 3.10 or newer (installed to PATH — check *"Add Python to PATH"* at install time).
- **LM Studio:** running locally with at least one LLM loaded ([lmstudio.ai](https://lmstudio.ai)). Benchmarks run through LM Studio's native OpenAI-compatible v1 API.

No administrator privileges required. Nothing is uploaded — all data stays on your machine.

---

## Quick start

### 1. Install dependencies

```bash
cd bench_llm
pip install -r requirements.txt
```

### 2. Start the dashboard

**Windows (one-click):** double-click

```
bench_llm/start_bench.bat
```

This checks Python and dependencies, then starts the server on `http://127.0.0.1:8000` and opens your browser.

**Manually:**

```bash
cd bench_llm
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open `http://127.0.0.1:8000`. The server binds to **loopback only** by design (see [Security](#security--local-runtime-boundary)).

### 3. Run a benchmark

1. Confirm the LM Studio URL (default `http://localhost:1234`) or enter your local endpoint.
2. Click **Refresh Models** to populate the dropdown from LM Studio's model list.
3. Choose a model, then launch either a **Standard Speed** run or a **Workflow** suite from the launcher.
4. Results appear on completion and are stored locally for later review.

---

## Model lifecycle

Model discovery and load/unload use **LM Studio's native v1 REST API**:

- `GET  /api/v1/models` — authoritative model list and state (used by *Refresh Models*).
- `POST /api/v1/models/load` — load a model (advisory context length at the standard target).
- `POST /api/v1/models/unload` — unload a model instance.

The dashboard never invents loaded-state; it reports what the runtime actually holds and issues only these two native mutations. LM Studio is the only supported backend — **Ollama, vLLM, and other providers are not supported.**

---

## Results

Three distinct result surfaces, each owned by one benchmark family (there is no generic mixed-Results product). They share one **Unified Results UI foundation** (RM-26-AA-0016): a single dark visual system (no light/dark toggle), shared navigation with benchmark-specific tabs/views, shared cards/modules and collapsible sections, an L0→L1→L2 drill-down convention (model family/version → configuration/quantization → individual run evidence), and one loading/error/N/A contract. The frontend formats and drills down only — the ranking read model stays authoritative, so there is no client-side benchmark scoring or dimension recomputation.

| URL | Family | Contents |
|-----|--------|----------|
| `/results` | Unified Results shell | Standard Speed history (point-level, newest first) plus the Overall / Speed / Workflow tabs, collapsible model drill-down, and Context & Intelligence placeholders. Dark visual system; composite score stays explicitly unavailable. |
| `/speed/results/{run_id}` | Standard Speed | Detail for a single run by context point (8K/16K/32K). |
| `/v2/results/{run_id}` | Workflow | Detail for a single Workflow run by suite and check. |

Delivered now: the unified Results shell, dark visual system, shared navigation/modules, and L0→L1→L2 convention. **Not yet delivered:** Context-degradation Results UI (RM-26-AA-0018), AI Intelligence Results (research), multi-model comparison view, and any Overall/composite score.

---

## Limitations

- **Configuration-dependent:** numbers depend on your specific model, quantization, hardware, LM Studio settings, and context size. Do not compare results across materially different configurations without care.
- **Standard Speed uses fixed points** (8K / 16K / 32K); it measures performance, never correctness or intelligence.
- **Workflow is deterministic correctness**, not a general-intelligence score — strong models can saturate it.
- **Context is not yet delivered** (planned).
- **AI Intelligence is not yet delivered** (research/planned) — do not treat it as available.
- **Windows-focused**; untested on macOS/Linux.

---

## Development / testing

Tests run **GPU-free and in-memory** where possible; a subset exercises the model-backed runners against a local LM Studio endpoint. Run from `bench_llm`:

```bash
# Fast local gate (~56s): offline / deterministic tests only — run on every commit.
cd bench_llm && python test_gate.py

# Full regression suite (~4-5 min, 627 tests): run before push/merge.
cd bench_llm && python test_gate.py full
```

Equivalently `python -m pytest` runs the full suite and `-m "not slow and not integration and not live"` selects the fast gate. The fast gate is broad (imports, read models, benchmark contracts, persistence round-trips, ownership/isolation, routing, security policy across all families) yet bounded; it supplements — never replaces — the authoritative full suite. See [`docs/architecture.md`](docs/architecture.md) (§Testing) for the tiered taxonomy and targeted per-family runs.

The full suite validates isolation, fail-closed behaviour, timeouts, backend-URL policy, LAN boundary, and result-compatibility contracts. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the canonical roadmap of delivered, planned, and research items.

---

## Security / local-runtime boundary

Solo Dev BenchLLM is a **local-only** tool:

- **No telemetry.** Nothing is collected or uploaded.
- **Loopback-only networking.** The dashboard binds `127.0.0.1` only; there is no LAN/`0.0.0.0` override.
- **Backend allow-list.** LM Studio / backend URLs default to loopback (`127.0.0.1` / `localhost`) and are validated before any request; arbitrary public or private destinations are rejected.
- **Isolated execution.** Model-generated code runs behind an execution boundary — isolated environment, disposable workspace, hard timeout, failing closed on startup error.

The detailed, authoritative statement lives in [`docs/SECURITY.md`](docs/SECURITY.md), which also documents a known platform limitation (this Windows dev host does not OS-block raw socket egress or absolute file access — stated as a boundary, not a sandbox claim).

---

## Roadmap

The single canonical roadmap is [`docs/ROADMAP.md`](docs/ROADMAP.md) (`RM-26-AA-*` items): delivered families (Standard Speed, Workflow), planned work (Context, AI Intelligence, docs, test-gate), and future research. Git history is implementation evidence; it is not the roadmap itself.
