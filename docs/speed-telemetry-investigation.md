# Speed Telemetry Investigation — Findings

**Status:** FINDINGS (evidence for time-series persistence decisions) · Runtime identity: `solo-dev-llm-bench` · Branch: `feat/benchmark-v2`

> This is a read-only investigation of what the current runtime actually exposes. It changes no
> code or schema. Its conclusions gate whether / how speed **time-series** persistence can be added
> (Results UI redesign §Speed telemetry task, and prerequisite for the repeatability contract).
> All claims below are traced to verified source in `src/`.

---

## 1. Direct answer to each investigation question

| Question | Finding | Evidence |
|----------|---------|----------|
| How frequently does LM Studio expose/update generation throughput? | **Once per completion**, as an aggregate scalar `tokens_per_second` inside `/api/v1/chat` response `stats`. No periodic/streaming cadence. | `benchmark.py:326`, `v2_quality_executor.py:~258/433` — all use `stream=False` and read `stats.get("tokens_per_second")`. |
| Does token-arrival timing provide better resolution? | **Not via the current native path.** The request uses `stream:false`; no token stream, no per-token timestamps are returned. Finer resolution would need a streaming call LM Studio exposes but this codebase never issues. | All chat payloads set `"stream": False` (`benchmark.py:291`, `v2_quality_executor.py:433`, `v2_quality_suite_runner.py:446`). No `streaming_events`/chunk handling exists anywhere in `src/`. |
| Which prompt-processing measurements are directly observable? | **Only**: `input_tokens` (prompt token count) and `time_to_first_token_seconds` (cold full-prefill TTFT). Prefill throughput is *derived* from those two (`tokens / ttft`) — computed, not reported. | `benchmark.py:327-328`, plus speed read-model derivation in `v2_speed_suite_runner.prefill_throughput()`. |
| Can reasoning/debug token throughput be captured independently? | **Only if the backend stats expose them.** The runner captures none today; Workflow's `_telemetry` reads an optional `reasoning_output_tokens` and records it only when present (`v2_quality_suite_runner._telemetry`). Speed never requests/records it. | `v2_quality_suite_runner.py` `_telemetry()` (reads `stats.get("reasoning_output_tokens")`, nullable). |
| Can we assume 1 Hz sampling? | **No.** There is no evidence of any 1 Hz or periodic sampler in the runtime. Generation throughput arrives exactly once per completed request — effectively a single sample per subtest, not a series. Any time-series persistence must be built on *client-side* instrumentation (see §3), which does **not** exist yet. | No sampling loop/timer anywhere in `src/` (`rg stream`, `rg telemetry_sample_count` → only schema/column definitions). |

---

## 2. What the current Speed run actually records

For each of the three canonical points (8K / 16K / 32K), one persisted row carries, per
iteration:

```jsonc
{
  "tokens_per_second":   <aggregate scalar from LM Studio stats>, // 1 sample/iteration
  "ttft_seconds":        <time_to_first_token_seconds>,           // cold full-prefill (iter 1, cache-busted)
  "input_tokens":        <prompt tokens> ,                        // authoritative runtime count
  "output_tokens":       <total_output_tokens>,
  "model_load_time_seconds": <optional>,
  "wall_time_seconds":   <client-measured elapsed for the single POST>
}
```

`run_benchmark` runs `iterations=5` (STANDARD_ITERATIONS) per point, but only iteration 1 is a
genuine cold full-prefill sample; iterations 2–5 are warm cache-reused samples contributing to the
warm generation-throughput average. So per point there is:

- **1 cold TTFT** (full prefill),
- **up to 4 warm generation throughput scalars**,
- **0 time-series points**.

This matches the repeatability contract intent (§3 below) but provides only scalar throughput, not a
curve.

---

## 3. Consequences for time-series persistence

1. **The current API shape yields no native token arrival-time series.** Token-arrival timing with
   sub-second resolution would require either (a) a streaming `/chat` call (`stream:true`) whose
   chunk timestamps the client records, or (b) server-side per-token event emission — neither of
   which this codebase exercises today.
2. **Client-side wall-clock is the only in-reach resolution knob without backend changes.** The
   request/response boundary is already timed client-side (`wall_time_seconds`, `perf_counter`). This
   gives per-request granularity (i.e. per-iteration), not intra-request token timing.
3. **Do NOT synthesise a 1 Hz series from the scalar.** Interpolating throughput samples into a fake
   time curve would violate the "nothing fabricated" contract (§0.6 of `docs/results-data-contract.md`).
   Record what is observed (per-iteration scalars) and mark finer resolution as absent.
4. **To capture real token-arrival timing** (if later desired), the planned change is: issue a
   streaming chat call for a dedicated telemetry pass, timestamp each chunk client-side, and persist
   those timestamps as a distinct evidence field — clearly separated from scored throughput so it can
   never masquerade as a score. This is out of scope for this investigation.

---

## 4. Directly observable prompt-processing measurements (authoritative list)

| Measurement | Source | Present today in Speed? | Notes |
|-------------|--------|--------------------------|-------|
| Prompt token count (`input_tokens`) | LM Studio stats | **Yes** | Authoritative; used for calibration + prefill derivation. |
| Cold full-prefill TTFT (`time_to_first_token_seconds`, iter 1) | LM Studio stats | **Yes** | Cache-busted per `(run_id, point)` so it is genuine prefill. |
| Prefill throughput (derived `input_tokens / ttft`) | computed | **Yes (derived)** | Never reported by backend; recomputed only from the two observed values. |
| Generation/decode throughput (`tokens_per_second`) | LM Studio stats | **Yes** | Scalar per iteration; warm average used as representative. |
| Per-request wall time | client `perf_counter` | **Yes** | Single-request granularity only. |
| Total invocation duration (`benchmark_duration_seconds`) | client `perf_counter` | **Yes** | Across all iterations of the point. |
| Reasoning/debug token throughput (`reasoning_output_tokens`) | LM Studio stats (optional) | **No** | Readable when exposed; not requested by Speed. Record absent otherwise. |

---

## 5. Recommendation (for later tasks, no action here)

- Keep speed persistence as **per-iteration scalars**, stored independently per run and per point
  (supports the cold/warm repeatability contract). Do **not** introduce a 1 Hz series now.
- Any future intra-request token-arrival time-series must be built on a **separate streaming telemetry
  pass** with its own evidence field, kept distinct from scored metrics and never converted into a
  score (see `docs/results-data-contract.md` §5/§6 eligibility + §10).
- Treat reasoning throughput as observable-only: capture when the backend reports it; otherwise `null`.

---

## 6. Trace index

| Claim | Source line(s) |
|-------|----------------|
| All chat calls use `stream=False`; aggregate stats read from body | `src/benchmark.py:291,326-330` |
| Same pattern in Quality executor / suite runner | `src/v2_quality_executor.py:258,433`; `src/v2_quality_suite_runner.py:446` |
| Reasoning tokens read only when present (nullable) | `src/v2_quality_suite_runner.py` `_telemetry()` |
| No streaming-chunk / per-token handling anywhere | `rg "stream"` in `src/` → only payload flags + unrelated text |
| No periodic sampler/timer; telemetry_sample_count is schema-only | `src/results.py` column defs; no loop found in `src/` |
