# Speed Benchmark Repeatability Contract (Design)

**Status:** DESIGN CONTRACT (implementation pending explicit approval) · Runtime identity: `solo-dev-llm-bench` · Branch: `feat/benchmark-v2`

> Design only. It specifies how the Standard Speed runner must record and separate runs so cold and
> warm measurements are never conflated. **No code, schema or test changes are made by this doc.**
> Implementation is blocked on explicit user approval (see §8), per scope-guardrail rules.

Grounded in: `docs/results-data-contract.md` (§5/§6 validity + eligibility, §10 telemetry), the
verified current runner (`src/v2_speed_suite_runner.py`, `src/benchmark.py`) and the speed telemetry
findings (`docs/speed-telemetry-investigation.md`).

---

## 1. Why this contract exists

The current Standard Speed runner executes `iterations=5` per canonical point (8K / 16K / 32K), then
collapses them into **one aggregate row per point** (`_aggregate_point()`): a single generation
throughput value derived from the warm average, backed by one cold full-prefill TTFT. That single-row
representation:

- blends cold and warm samples (the cold full-prefill TTFT with three+ warm cache-reused decodes),
- hides within-run variance, and
- makes it impossible to report *distinct* cold vs warm throughput without re-deriving a misleading
  combined average.

The repeatability contract requires the three runs per bucket to be stored **independently** so each
measurement keeps its own status, telemetry and provenance.

---

## 2. Run cadence (per tested context bucket)

For **each** canonical point in `CANONICAL_CONTEXT_POINTS` (`8192, 16384, 32768`):

| # | Run | Role | Cold or warm |
|---|-----|------|--------------|
| 1 | **cold run** | Full prefill / cold model state — the authoritative TTFT + genuine cold decode baseline | `cold` (iter 1, cache-busted) |
| 2 | **warm run A** | Repeated throughput sample with KV-cache reused (model stays loaded) | `warm` |
| 3 | **warm run B** | Second repeated throughput sample for variance / repeatability check | `warm` |

- The cold run is measured exactly as today: iteration 1 with the per-`(run_id, point)` cache-busting
  prefix so its TTFT is genuine full-prefill (never a cached hit). This is preserved unchanged.
- Warm runs are additional independent POSTs reusing the plain payload; they contribute generation
  throughput only, **never** feed the cold full-prefill TTFT.

---

## 3. Independent storage requirement

Each of the three runs is persisted as its **own distinct record** under the same `run_id`, carrying
its own subtest key so it can be recovered independently:

```
<benchmark_family>:<run_id>:speed:<context_point>:[cold|warmA|warmB]
```

- No merging of the three into a single point row. A read model exposes all three per bucket, each
  with its own metrics + status + telemetry + termination reason.
- The three runs share **one** `run_id` (the benchmark execution identity) so grouping stays correct;
  they differ by subtest key and cold/warm tag.

---

## 4. Metrics kept separate per run (never averaged across cold+warm)

Per stored run, record the following fields **independently**. Cold and warm are never combined into
a single average value:

| Field | Cold run | Warm runs A / B |
|-------|----------|-----------------|
| `ttft_seconds` (cold full-prefill) | **Authoritative** — reported, feeds prefill throughput | Not a cold sample; record but do not use for prefill derivation. |
| prompt-processing throughput (`input_tokens / ttft`) | Derived from the cold TTFT only | N/A for warm (no cold prefill). |
| generation/decode throughput (`tokens_per_second`) | Cold decode baseline | **Representative** values; reported per run — variance between A and B is inspectable. |
| reasoning/debug throughput | Where observable, separate field (§5) | Same — independent, never merged with generation. |
| `wall_time_seconds` (per request) | Its own value | Each its own value. |
| `benchmark_duration_seconds` (invocation) | Per run | Each its own. |
| resource telemetry (`telemetry` object) | Its own sample set (§5, §11 of data contract) | Each its own — preserves peak/avg per run. |
| status / termination_reason | Independent per run | Independent per run. |

The read surface exposes **three** throughput values per warm bucket plus the cold baseline so a
consumer can compute genuine repeatability (e.g. A vs B spread) without the runner inventing one.

---

## 5. Reasoning / debug throughput

Where the backend stats expose `reasoning_output_tokens` independently, capture it as a distinct field
per run and **never fold it into generation throughput**. When not observable, record `null` (not zero)
— see telemetry findings §4/§6. This is a new optional capture; today Speed records none.

---

## 6. Cold vs warm — explicit never-do list

- Never compute one "average tokens/sec" across the cold run and the warm runs for display or scoring.
- Never let a warmed calibration slot mask the cold full-prefill TTFT (the existing cache-buster rule
  is preserved; see `v2_speed_suite_runner._speed_cache_buster`).
- Never drop an unsuccessful warm/cold run silently — preserve it under its own subtest key with status
  + termination reason, hidden behind *Show unsuccessful* in the UI (§ §6 of data contract).

---

## 7. Impact on existing surfaces (only if implemented)

- `load_speed_run_by_id()` / `_shape_speed_history_point()`: a run would now carry **3 records per
  point** instead of 1; the read model must group by `(run_id, context_point)` and expose the three
  runs + their status rather than a single collapsed point. Integrity checks (duplicate-point guards)
  must be relaxed to accept one cold + two warm per canonical point — otherwise they currently raise.
- `/api/results` history and `results.js`: card rendering should show the representative throughput with
  an explicit *cold / warm* label; avoid presenting a single blended tok/s as if it were one measurement.

> These are implementation consequences, **not** changes applied here. They require the runner + read
> model edits that are gated on approval (§8). The persistence layer already stores per-row arbitrary
> fields, so storage itself needs no schema churn beyond any new optional columns (e.g. reasoning tokens).

---

## 8. Approval gate (blocked)

This is a **design contract only**. Implementation changes the runner iteration strategy and read-model
grouping; it touches scoring-eligible data, so it requires explicit user approval before touching code,
schema or tests. Before that:

- Current behaviour is unchanged (5 iterations collapsed to one row per point).
- No new columns are added; no existing rows are rewritten (byte-for-byte preservation rule stands).

Open decision for approver: keep `iterations=5` and record **cold + up to 2 warm** of them explicitly,
or switch to a fixed **1 cold + 2 warm = 3 POSTs** per point (matching the contract literally). The
contract mandates ≥1 cold + ≥2 warm; exact iteration count is the caller's choice as long as independence
and separation hold.
