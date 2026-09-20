"""Standard Speed Suite runner (Act 17).

Executes the fixed *standard* Speed contract -- three binary context-pressure points:

    8K · 16K · 32K        (8192 · 16384 · 32768 tokens of input pressure)

measuring TTFT, prefill throughput and generation/decode throughput at each point.

This is **not** the legacy launcher benchmark. The legacy ``/api/evaluation/run``
speed tests run three small *fixture* prompts (~267 / ~1,359 / ~5,318 tokens) -- those
are deliberately **not** the standard 8K/16K/32K bins and are never reused here.

What this module does (and only):

* Builds a deterministic, content-insensitive payload whose input/token load targets
  each fixed context point (so it measures *real* context pressure, not merely an 8K
  configuration with a tiny prompt).
* Wraps the existing, authoritative benchmark engine :func:`src.benchmark.run_benchmark`
  for request + timing + token capture -- no hand-rolled HTTP or timers.
* Marks a point ``unsupported`` (and persists that status) when the model's effective
  loaded context is below the target; it never silently shrinks the target.
* Persists one durable row per attempted point to the shared :class:`ResultsStore` so
  the existing ``/results`` page and the Act 13 read model consume it unchanged.

What this module does **not** do: change any benchmark mechanic, expose custom context
bins / iteration counts / output settings (those belong to Advanced / Custom), or touch
the locked Workflow quality executor.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

# --- Reusable primitives (read-only; no benchmark-mechanic mutation). ---------------
from src.benchmark import (  # noqa: E402
    run_benchmark,
    resolve_persisted_quantization,
    resolve_context_capacity,
    resolve_loaded_instance_config,
)
# Shared cross-suite concurrency guard (Act 17). Launch registers the child handle here
# so Workflow and Speed reject overlapping launches bidirectionally.
import src.standard_run_guard as standard_run_guard  # noqa: E402

# Canonical benchmark reasoning rule: throughput metrics are measured with reasoning
# explicitly OFF (identical to run_benchmark's payload and the legacy speed path).
from src.results import BENCHMARK_REASONING_MODE  # noqa: E402
# Run-level execution provenance (Act: reproducibility). Stdlib-only capture module;
# captured once per run and attached to every row of that run via the shared identity.
from src.provenance import capture_provenance as _capture_provenance, to_json as _provenance_to_json

# Migrated from the retired legacy-Evaluation ``evaluation_prompts`` module (Act 24).
# Retained here as the sole active consumer of this lightweight char/token estimator so no
# dead "evaluation" module survives solely to host a Speed calibration helper.
def estimate_tokens(text: str) -> int:
    """Rough token-count estimate for *text*.

    Uses a simple characters-per-token heuristic (~4 characters per token), matching the
    behaviour that previously lived in the retired ``evaluation_prompts`` module. This is
    only an estimation-tolerance bound; actual loaded tokens remain authoritative from the
    runtime.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Standard Speed contract constants.
# ---------------------------------------------------------------------------

# Binary context bins -- NOT decimal 8,000/16,000/32,000. These are the authoritative
# standard points and are never derived from validator output or user input.
CANONICAL_CONTEXT_POINTS: tuple[int, ...] = (8192, 16384, 32768)

# Human-facing labels for each canonical point.
CONTEXT_POINT_LABELS: dict[int, str] = {8192: "8K", 16384: "16K", 32768: "32K"}

# Standard Speed repeatability contract (Evidence-First Redesign). Each SUPPORTED point
# records exactly ONE independent run per stage -- 1 cold (cache-busted full-prefill) +
# 2 warm (warm_a / warm_b) -- persisted as separate rows so cold and warm are never averaged
# together. The two warm runs form a repeatability pair for the generation-throughput signal;
# their mean is the reported warm figure, but every run's telemetry stays independently
# recoverable under its own speed_run_stage (see _persist_speed_runs).
SPEED_COLD_RUNS: int = 1
SPEED_WARM_RUNS: int = 2
SPEED_TOTAL_RUNS_PER_POINT: int = SPEED_COLD_RUNS + SPEED_WARM_RUNS  # == 3

# Stage tags persisted in speed_run_stage to distinguish the independent runs of one point.
SPEED_STAGE_COLD = "cold"
SPEED_STAGE_WARM_A = "warm_a"
SPEED_STAGE_WARM_B = "warm_b"

# Fixed generation/target output budget for stable decode throughput measurement,
# identical across all points and models/configurations. Deliberately NOT inherited
# from the Workflow Suite's 75_percent_context policy -- Speed and Workflow differ.
STANDARD_OUTPUT_TOKENS: int = 512

# Speed metric measurement version persisted with each corrected aggregate row (additive,
# backward-safe). Historical rows carry no value (legacy semantics); every run produced by
# this runner persists SPEED_METRIC_VERSION so the read model can distinguish corrected
# full-prefill measurements from legacy cached-TTFT ones.
#   1 = legacy        : pre-cache-correction cached-TTFT prefill.
#   2 = corrected      : Act 20 fixed prefill semantics (cold full-prefill TTFT).
#   3 = calibrated     : Act 21 sizes each deterministic payload so its runtime/tokenizer-
#                        reported input_tokens lands near the canonical target (8K/16K/32K)
#                        rather than undershooting to ~68% of nominal.
# A new version is justified because pre- and post-calibration runs both claim 8K/16K/32K
# labels while their ACTUAL input sizes differ materially (~5587 vs ~8192 at 8K); the marker
# lets consumers tell the sizing era apart without re-deriving it. 2 and 3 are both non-
# legacy (neither is a cached-TTFT artifact) per the read model's exemption rule.
SPEED_METRIC_VERSION: int = 3

# Target-tolerance band (fraction of nominal) for Standard Speed calibration. Act 21 sizes
# each deterministic payload so its runtime/tokenizer-reported input_tokens lands within this
# band of the canonical target; exact equality is not required because chat wrappers/templates
# add a few tokens and tokenization differs across model families.
SPEED_TARGET_TOLERANCE: float = 0.02

# Upper bound on deterministic calibration passes per point when sizing filler from the
# authoritative runtime input-token count. Bounded (never an unbounded search): convergence
# typically takes 1-3 POSTs; this cap guarantees termination even under a noisy metric.
SPEED_MAX_CALIBRATION_ITERATIONS: int = 6

# Generation budget for calibration-only chat calls. Kept minimal so token-size discovery
# does not spend meaningful decode/KV work; the authoritative full-prefill sample (iteration
# 1, cache-busted prefix) is never among these calls -- see _speed_calibrated_filler_copies.
SPEED_CALIBRATION_OUTPUT_TOKENS: int = 4

# Distinctive prompt marker for calibration chat calls. It shares only a ~1-character common
# prefix with both the authoritative full-prefill buster ("[speed-run:") and the warm payload
# header ("[standard-speed"), so LM Studio's longest-common-prefix KV-cache reuse cannot let a
# warmed calibration slot mask the genuine cold full-prefill TTFT.
_SPEED_CALIBRATION_MARKER = "[calib-prefill-check:{run_id}:point:{target}]\n"

# Status labels for an attempted point.
POINT_COMPLETED = "completed"
POINT_UNSUPPORTED = "unsupported"
POINT_FAILED = "failed"


# ---------------------------------------------------------------------------
# Deterministic context-pressure payload construction.
# ---------------------------------------------------------------------------

_PADDING_SENTENCE = (
    "Deterministic padding token stream used only to create input-context pressure for "
    "the standard speed benchmark; it contains no evaluatable content and is repeated "
    "verbatim to reach the target context size without injecting random corpus material. "
)


def _filler_for_copies(n: int) -> str:
    """Return *n* concatenated copies of the fixed padding sentence (content-neutral)."""
    return "".join([_PADDING_SENTENCE] * n) if n > 0 else ""


def _estimate_filler_copies(target_tokens: int) -> int:
    """Deterministic copy count for *target_tokens* via the lightweight char/token estimate.

    PURE heuristic used ONLY as a bounded starting point / graceful fallback. It is never
    authoritative sizing (that comes from the runtime input-token count in Act 21), and it is
    intentionally model-agnostic so the builder stays usable without any network call and unit
    tests remain deterministic.
    """
    header = f"[standard-speed {target_tokens} token context-pressure point]\n"
    est = estimate_tokens(header)
    block_estimate = estimate_tokens(_PADDING_SENTENCE) or 1
    n = 0
    while est < target_tokens:
        n += 1
        est += block_estimate
    return max(1, n)


def build_context_pressure_prompt(target_tokens: int, filler: Optional[str] = None) -> str:
    """Return a deterministic, content-insensitive payload targeting *target_tokens*.

    When *filler* is supplied (Act 21 calibration), the header is combined with exactly that
    precomputed filler so callers can size input from the authoritative runtime token count.
    Otherwise the filler is estimated deterministically (no network) -- a bounded tolerance is
    expected and never misreported as exact; actual loaded prompt tokens are authoritative and
    recorded separately. Content stays neutral: no model-specific identifiers, no evaluatable
    text, no randomness, so the payload is safe for every model.
    """
    header = f"[standard-speed {target_tokens} token context-pressure point]\n"
    if filler is not None:
        return header + filler
    return header + _filler_for_copies(_estimate_filler_copies(target_tokens))


async def _count_input_tokens(
    run_id: str,
    model: str,
    base_url: str,
    prompt: str,
) -> Optional[int]:
    """Return the authoritative runtime input-token count for *prompt* (or ``None``).

    A single minimal chat call to the SAME endpoint that measures Speed reuses its exact
    tokenization/reporting, so the returned ``input_tokens`` is the same value ultimately
    recorded on the run. Generation budget is tiny and the probe carries a distinct cache-
    marker (see ``_speed_calibrated_filler_copies``) so it can never warm a slot reused by the
    authoritative cold full-prefill sample -- calibration must not undo Act 20.
    """
    try:
        bench = await run_benchmark(
            lm_studio_url=base_url,
            model=model,
            prompt=prompt,
            full_prefill_prompt=None,
            iterations=1,
            max_tokens=SPEED_CALIBRATION_OUTPUT_TOKENS,
            temperature=0.0,
            hardware_label="",
            execution_environment="Local",
            connection_type="",
            prompt_name=f"[calib {run_id}]",
        )
    except Exception:  # pragma: no cover - defensive; calibration must never abort the suite
        return None
    runs = bench.get("runs") if isinstance(bench, dict) else None
    if not runs:
        return None
    first = runs[0]
    count = first.get("input_tokens", 0) if isinstance(first, dict) else 0
    return int(count) if isinstance(count, (int, float)) and count > 0 else None


async def _speed_calibrated_filler_copies(
    run_id: str,
    model: str,
    base_url: str,
    point: int,
) -> int:
    """Pick the filler-copy count whose payload lands within tolerance of *point* tokens.

    Bounded deterministic search over the AUTHORITATIVE runtime input-token count (no hardcoded
    per-model char/token ratio -- tokenization is measured against THIS model at runtime). Each
    probe shares only a ~1-character common prefix with the cold full-prefill buster and the
    warm header, so warmed calibration slots cannot mask the genuine cold full-prefill TTFT.
    Convergence uses a Newton-like step on the measured tokens-per-copy slope. Falls back to
    the estimate-based copy count on any error or if no convergence is reached within the cap.
    """
    target_lo = point * (1.0 - SPEED_TARGET_TOLERANCE)
    target_hi = point * (1.0 + SPEED_TARGET_TOLERANCE)
    n = _estimate_filler_copies(point)  # bounded deterministic starting guess

    for _ in range(SPEED_MAX_CALIBRATION_ITERATIONS):
        prompt = (
            _SPEED_CALIBRATION_MARKER.format(run_id=run_id, target=point)
            + build_context_pressure_prompt(point, filler=_filler_for_copies(n))
        )
        actual = await _count_input_tokens(run_id, model, base_url, prompt)
        if actual is None:  # probe failed (network/exception) -- retry next iteration
            continue
        if target_lo <= actual <= target_hi:
            return n
        slope = (actual / n) if (n > 0) else (estimate_tokens(_PADDING_SENTENCE) or 1)
        delta = int(round((point - actual) / slope))
        candidate = n + delta
        if abs(candidate - n) <= 1:  # near convergence -- step by 1 to avoid overshooting
            candidate = n + (1 if point > actual else -1)
        n = max(1, candidate)

    return n


def _speed_cache_buster(run_id: str, target_tokens: int) -> str:
    """Deterministic prefix that defeats LCP / KV-cache reuse for the full-prefill sample.

    Standard Speed reuses a single deterministic prompt per point; LM Studio selects slots
    by longest-common-prefix, so an uncached first request would otherwise be rejected in
    favour of a cached slot and yield a fake prefill rate. This prefix is placed at the
    START of the authoritative full-prefill prompt (before any shared filler) so it is
    unique per ``(run_id, point)`` -- deterministic for the same run+point, different across
    run IDs and across 8K/16K/32K -- while being negligible in size relative to the payload.
    A suffix would be insufficient because prefix reuse only matches from the start.
    """
    return f"[speed-run:{run_id}:point:{target_tokens}]\n"


# ---------------------------------------------------------------------------
# Metric definitions (authoritative project semantics, reused verbatim).
# ---------------------------------------------------------------------------

def prefill_throughput(actual_prompt_tokens: Optional[int], ttft_seconds: Optional[float]) -> Optional[float]:
    """Derive prefill throughput using the project's authoritative definition.

    ``prefill_tokens_per_second = actual_prompt_tokens / ttft_seconds`` -- exactly what
    the locked V2 suite runner (:func:`src.v2_quality_suite_runner._telemetry`) and the
    Act 13 speed read model use. TTFT is kept separate from this value (see below).
    Returns ``None`` when either input is missing/non-positive so a bad/zero TTFT never
    divides to a misleading rate.
    """
    if not isinstance(actual_prompt_tokens, int) or isinstance(actual_prompt_tokens, bool) \
            or actual_prompt_tokens <= 0:
        return None
    if not isinstance(ttft_seconds, (int, float)) or isinstance(ttft_seconds, bool) \
            or ttft_seconds <= 0:
        return None
    return round(float(actual_prompt_tokens) / float(ttft_seconds), 1)


def _effective_capacity(model_max_context: Optional[int], loaded_context: Optional[int]) -> Optional[int]:
    """Largest positive known capacity among model max / loaded context (or ``None``)."""
    known = [c for c in (model_max_context, loaded_context)
             if isinstance(c, int) and not isinstance(c, bool) and c > 0]
    return max(known) if known else None


def _capacity_supports(capacity: Optional[int], target: int) -> bool:
    """True when the available capacity can host *target* tokens of pressure."""
    return capacity is not None and capacity >= target


# ---------------------------------------------------------------------------
# Row construction helpers (reuse existing ResultsStore columns + additive ones).
# ---------------------------------------------------------------------------

def _identity_row(
    run_id: str,
    model: str,
    *,
    quantization: str,
    loaded_context: Optional[int],
    model_max_context: Optional[int],
    inst: dict[str, Any],
    hardware_label: str,
) -> dict[str, Any]:
    """Build the shared identity/config portion of a persisted speed row.

    Reuses existing ResultsStore columns (quantization, loaded context, reasoning mode,
    flash-attention / KV-cache / batch / MTP-speculative instance config) so comparisons
    across runs stay interpretable and never fabricate unavailable metadata.
    """
    return {
        "run_id": run_id,
        "model_key": model,
        "model_display_name": model,
        "model_quantization": quantization or "",
        "hardware_label": hardware_label or "",
        "execution_environment": "Local",
        "connection_type": "",
        "reasoning_mode": BENCHMARK_REASONING_MODE,
        "loaded_context": loaded_context,
        "model_max_context": model_max_context,
        # Loaded-instance inference configuration (Act 11B fields), best-effort.
        "flash_attention": inst.get("flash_attention"),
        "offload_kv_cache_to_gpu": inst.get("offload_kv_cache_to_gpu"),
        "eval_batch_size": inst.get("eval_batch_size"),
        "physical_batch_size": inst.get("physical_batch_size"),
        "parallel": inst.get("parallel"),
        "num_experts": inst.get("num_experts"),
        "speculative_draft_mtp": inst.get("speculative_draft_mtp"),
        "speculative_draft_simple": inst.get("speculative_draft_simple"),
        "speculative_draft_model": inst.get("speculative_draft_model") or "",
        "speculative_draft_max_tokens": inst.get("speculative_draft_max_tokens"),
        "speculative_draft_min_tokens": inst.get("speculative_draft_min_tokens"),
        "speculative_draft_min_continue_probability": inst.get("speculative_draft_min_continue_probability"),
        "kv_cache_k_quantization": inst.get("kv_cache_k_quantization"),
        "kv_cache_v_quantization": inst.get("kv_cache_v_quantization"),
        "kv_cache_quantization_source": inst.get("kv_cache_quantization_source") or "unknown",
    }


def _is_cold_run(run: dict) -> bool:
    """True when *run* marks itself the cold (full-prefill) sample -- i.e. iteration 1.

    run_benchmark labels iteration 1 ``cold``; Standard Speed pairs that request with a
    cache-busting prompt so it is a genuine full-prefill measurement rather than a cached
    reuse of an earlier point's KV slot.
    """
    return str(run.get("cold_or_warm") or "").strip().lower() == "cold"


# Stage tags persisted in speed_run_stage to distinguish the independent runs of one point
# are declared above (SPEED_STAGE_COLD / SPEED_STAGE_WARM_A / SPEED_STAGE_WARM_B).
def _stage_label_for(index: int) -> str:
    """Map a 0-based run index within a supported point to its persisted stage tag.

    The first run is the cold (cache-busted full-prefill) sample; runs two and three are the
    warm repeatability pair (warm_a / warm_b). Anything beyond SPEED_TOTAL_RUNS_PER_POINT keeps
    a stable fallback tag so extra rows never raise -- they simply never appear under this
    contract.
    """
    return {0: SPEED_STAGE_COLD, 1: SPEED_STAGE_WARM_A, 2: SPEED_STAGE_WARM_B}.get(
        index, f"warm_extra_{index}"
    )


def _as_number(value) -> Optional[float]:
    """Return *value* as int/float when it is a real number, otherwise None.

    Booleans are rejected (``True``/``False`` must never be treated as 1.0/0.0 numbers).
    Used for defensive coercion of persisted telemetry so absent values degrade to None and
    never divide/coerce into a misleading figure.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _aggregate_point(bench: dict[str, Any]) -> dict[str, Any]:
    """Collapse a :func:`run_benchmark` result into one representative point summary.

    Used to build the CLI/route response for a supported point whose runs are persisted as
    independent stage rows (see :func:`_persist_speed_runs`). Generation throughput uses the
    warm-iteration average when available (iteration 1 is ``cold`` and carries model-load
    variance); it falls back to the all-iteration average. Primary TTFT comes from the
    authoritative FULL-PREFILL sample -- the cold (iteration 1) request, which Standard Speed
    pairs with a cache-busting prompt so its time-to-first token is genuine full-prefill rather
    than a cached hit; an average of warm (cache-reused) TTFTs must never feed prefill
    throughput. Actual prompt tokens come from the cold run; completion tokens use the final
    iteration as a representative generation length.
    """
    runs = bench.get("runs", []) or []
    warm = bench.get("warm_aggregate", {}) or {}
    overall = bench.get("aggregate", {}) or {}

    tps = warm.get("avg_tokens_per_second")
    if not isinstance(tps, (int, float)) or tps <= 0:
        tps = overall.get("avg_tokens_per_second")

    # Full-prefill sample only -- never the warm/cache-reused TTFT average.
    cold_runs = [r for r in runs if _is_cold_run(r)]
    ttft = cold_runs[0].get("ttft_seconds") if cold_runs else (runs[0].get("ttft_seconds") if runs else 0)

    actual_prompt_tokens = runs[0].get("input_tokens", 0) if runs else 0
    completion_tokens = runs[-1].get("output_tokens", 0) if runs else 0
    wall = bench.get("benchmark_duration_seconds", 0) or 0

    return {
        "status": POINT_COMPLETED,
        "generation_tokens_per_second": round(float(tps), 2) if isinstance(tps, (int, float)) else 0,
        "ttft_seconds": round(float(ttft), 4) if isinstance(ttft, (int, float)) else None,
        "actual_prompt_tokens": int(actual_prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "prefill_tokens_per_second": prefill_throughput(actual_prompt_tokens, ttft),
        "wall_time_seconds": round(float(wall), 2),
    }


def _persist_speed_runs(store, identity: dict, point: int, label: str, bench: dict[str, Any]) -> dict[str, Any]:
    """Persist one independent row per run (1 cold + 2 warm) for a supported point.

    Each of the SPEED_TOTAL_RUNS_PER_POINT runs is written as its own record tagged with a
    ``speed_run_stage`` so cold and warm are never averaged together at write time or in the
    read model. The cold run carries the authoritative full-prefill TTFT and derived prefill
    throughput; warm runs keep their raw TTFT under ``warm_ttft_seconds`` (diagnostic only --
    never fed into the cold full-prefill derivation). Every row records its own generation
    throughput, wall time and optional reasoning tokens (captured only when the backend reports
    it, else null/NOT REPORTED).

    Returns a point-level summary for the CLI/route response: cold full-prefill TTFT/prefill
    plus the warm-only mean of the repeatability pair (cold excluded -- never blended with warm).
    """
    runs = bench.get("runs", []) if isinstance(bench, dict) else []
    for index in range(min(SPEED_TOTAL_RUNS_PER_POINT, len(runs))):
        r = runs[index]
        stage = _stage_label_for(index)
        is_cold = index == 0
        tps = _as_number(r.get("tokens_per_second"))
        ttft = _as_number(r.get("ttft_seconds"))
        input_tokens = _as_number(r.get("input_tokens"))
        completion_tokens = _as_number(r.get("output_tokens"))
        wall = _as_number(r.get("wall_time_seconds"))
        if wall is None:
            wall = _as_number(bench.get("benchmark_duration_seconds")) if isinstance(bench, dict) else None
        reasoning = r.get("reasoning_output_tokens", None)

        row = dict(identity)
        row.update({
            "iteration": index + 1,
            "speed_run_stage": stage,
            "cold_or_warm": SPEED_STAGE_COLD if is_cold else "warm",
            "tokens_per_second": round(tps, 2) if tps is not None else 0.0,
            "ttft_seconds": round(ttft, 4) if ttft is not None else None,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(completion_tokens or 0),
            "model_load_time_seconds": r.get("model_load_time_seconds"),
            "wall_time_seconds": round(wall, 2) if wall is not None else 0.0,
            # Optional reasoning-token capture: only when the backend reports it; otherwise null.
            "reasoning_output_tokens": reasoning if isinstance(reasoning, int) and not isinstance(reasoning, bool) else None,
            "prompt_name": f"{label} ({point} tokens) [{stage}]",
            "max_output_tokens": STANDARD_OUTPUT_TOKENS,
            "temperature": 0.0,
            "target_context_tokens": point,
            "context_point": label,
            # Warm runs keep their raw TTFT as diagnostic evidence; the cold run owns full-
            # prefill TTFT + derived prefill throughput (set below). Never blend stages.
            "warm_ttft_seconds": (round(ttft, 4) if (not is_cold and ttft is not None) else None),
            "speed_point_status": POINT_COMPLETED,
            "speed_metric_version": SPEED_METRIC_VERSION,
        })
        # Cold run: authoritative full-prefill TTFT + derived prefill throughput. Warm runs are
        # NOT cold samples -> no prefill derivation (prefill stays a cold-only signal).
        row["prefill_tokens_per_second"] = (
            prefill_throughput(int(input_tokens or 0), ttft) if is_cold else None
        )
        try:
            store.add_run(row)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"warning: failed to persist {label} [{stage}]: {exc}", file=sys.stderr)

    return _aggregate_point(bench)
    """Collapse a :func:`run_benchmark` result into one representative point snapshot.

    Generation throughput uses the warm-iteration average when available (iteration 1 is
    ``cold`` and includes model-load variance), falling back to the all-iteration average.
    Primary TTFT comes from the authoritative FULL-PREFILL sample -- the cold (iteration 1)
    request, which Standard Speed pairs with a cache-busting prompt so its time-to-first
    token is genuine full-prefill rather than a cached hit; an average of warm (cache-reused)
    TTFTs must never feed prefill throughput. Actual prompt tokens are constant across
    iterations (same deterministic payload) so the first iteration's value is representative;
    completion tokens use the final iteration as a representative generation length.
    """
    runs = bench.get("runs", []) or []
    warm = bench.get("warm_aggregate", {}) or {}
    overall = bench.get("aggregate", {}) or {}

    tps = warm.get("avg_tokens_per_second")
    if not isinstance(tps, (int, float)) or tps <= 0:
        tps = overall.get("avg_tokens_per_second")

    # Full-prefill sample only -- never the warm/cache-reused TTFT average.
    cold_runs = [r for r in runs if _is_cold_run(r)]
    ttft = cold_runs[0].get("ttft_seconds") if cold_runs else (runs[0].get("ttft_seconds") if runs else 0)

    actual_prompt_tokens = runs[0].get("input_tokens", 0) if runs else 0
    completion_tokens = runs[-1].get("output_tokens", 0) if runs else 0
    wall = bench.get("benchmark_duration_seconds", 0) or 0

    return {
        "generation_tokens_per_second": round(float(tps), 2) if isinstance(tps, (int, float)) else 0,
        "ttft_seconds": round(float(ttft), 4) if isinstance(ttft, (int, float)) else None,
        "actual_prompt_tokens": int(actual_prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "prefill_tokens_per_second": prefill_throughput(actual_prompt_tokens, ttft),
        "wall_time_seconds": round(float(wall), 2),
    }


# ---------------------------------------------------------------------------
# Small result document (returned to the CLI/route layer; not persisted directly).
# ---------------------------------------------------------------------------

@dataclass
class SpeedPointResult:
    context_point: str
    target_context_tokens: int
    status: str
    actual_prompt_tokens: Optional[int] = None
    ttft_seconds: Optional[float] = None
    prefill_tokens_per_second: Optional[float] = None
    generation_tokens_per_second: Optional[float] = None
    completion_tokens: Optional[int] = None
    wall_time_seconds: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_point": self.context_point,
            "target_context_tokens": self.target_context_tokens,
            "status": self.status,
            "actual_prompt_tokens": self.actual_prompt_tokens,
            "ttft_seconds": self.ttft_seconds,
            "prefill_tokens_per_second": self.prefill_tokens_per_second,
            "generation_tokens_per_second": self.generation_tokens_per_second,
            "completion_tokens": self.completion_tokens,
            "wall_time_seconds": self.wall_time_seconds,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

def _get_store():
    """Return the shared ResultsStore singleton (imported lazily to avoid import side effects)."""
    import src.app_state

    return src.app_state.results_store


def generate_speed_run_id() -> str:
    """Server-owned run id with a distinct standard-speed prefix.

    Distinct from the Workflow ``run-`` convention and legacy UUID runs so grouping /
    display never conflates suites, while remaining filesystem-safe (hex only).
    """
    return "speed-" + os.urandom(6).hex()


async def run_speed_suite(
    lm_studio_url: str,
    model: str,
    hardware_label: str = "",
    *,
    run_id: Optional[str] = None,
    store=None,
) -> dict[str, Any]:
    """Execute the standard Speed Suite and persist one durable row per point.

    ``store`` may be injected (tests pass an in-memory fake); when omitted the shared
    :class:`ResultsStore` singleton is used so a detached ``python -m src v2-speed``
    process leaves durable rows behind for the status route / ``/results`` page.

    Returns a summary document with per-point statuses and metrics. Does not perform any
    benchmark-mechanic mutation -- it only composes :func:`run_benchmark` results, applies
    the fixed context-pressure contract and persists identity + measured telemetry.
    """
    if model is None or not isinstance(model, str) or not model.strip():
        raise ValueError("A model must be selected to run the Speed suite.")

    store = store or _get_store()
    base_url = (lm_studio_url or "").strip().rstrip("/")
    rid = (run_id or "").strip() or generate_speed_run_id()
    clean_model = model.strip()

    # --- Resolve identity/config metadata ONCE for the whole suite. ---
    quantization = await resolve_persisted_quantization(base_url, clean_model)
    ctx = await resolve_context_capacity(base_url, clean_model)
    inst = await resolve_loaded_instance_config(base_url, clean_model)
    loaded_context = inst.get("loaded_context") or ctx.get("loaded_context")
    model_max_context = ctx.get("model_max_context")
    effective_capacity = _effective_capacity(model_max_context, loaded_context)
    identity = _identity_row(
        rid, clean_model,
        quantization=quantization, loaded_context=loaded_context,
        model_max_context=model_max_context, inst=inst, hardware_label=hardware_label,
    )

    # --- Run-level execution provenance (Act: reproducibility). Captured ONCE for the
    # whole suite and attached to every row of this run via the shared identity dict.
    # Hardware/runtime/model/inference are captured once -- never recomputed per point or
    # per stage. The same JSON-native object is what Speed's flat store persists under
    # ``provenance_json``; Workflow/Context attach the nested object directly.
    try:
        prov_model_config = {
            "model_key": clean_model,
            "model_quantization": quantization,
            "loaded_context": loaded_context,
            "model_max_context": model_max_context,
            "reasoning_mode": BENCHMARK_REASONING_MODE,
            "flash_attention": inst.get("flash_attention"),
            "offload_kv_cache_to_gpu": inst.get("offload_kv_cache_to_gpu"),
            "eval_batch_size": inst.get("eval_batch_size"),
            "physical_batch_size": inst.get("physical_batch_size"),
            "parallel": inst.get("parallel"),
            "num_experts": inst.get("num_experts"),
            "speculative_draft_mtp": inst.get("speculative_draft_mtp"),
            "speculative_draft_simple": inst.get("speculative_draft_simple"),
            "speculative_draft_model": inst.get("speculative_draft_model") or None,
        }
        identity["provenance_json"] = _provenance_to_json(
            _capture_provenance(
                lm_studio_url=base_url,
                model_identifier=clean_model,
                model_config=prov_model_config,
            )
        )
    except Exception as exc:  # pragma: no cover - defensive; provenance must not abort a run
        print(f"warning: failed to capture speed run provenance: {exc}", file=sys.stderr)
        identity["provenance_json"] = None

    points: list[SpeedPointResult] = []
    for point in CANONICAL_CONTEXT_POINTS:
        label = CONTEXT_POINT_LABELS[point]
        if not _capacity_supports(effective_capacity, point):
            # Effective/loaded context is below the target -- mark unsupported explicitly
            # and persist that status; never silently shrink the target.
            row = dict(identity)
            row.update({
                "iteration": SPEED_TOTAL_RUNS_PER_POINT,
                "cold_or_warm": "",
                "speed_run_stage": "",
                "tokens_per_second": 0,
                "ttft_seconds": None,
                "input_tokens": None,
                "output_tokens": None,
                "wall_time_seconds": 0,
                "prompt_name": f"{label} (unsupported)",
                "max_output_tokens": STANDARD_OUTPUT_TOKENS,
                "temperature": 0.0,
                "target_context_tokens": point,
                "context_point": label,
                "speed_point_status": POINT_UNSUPPORTED,
            })
            try:
                store.add_run(row)
            except Exception as exc:  # pragma: no cover - defensive; persistence must not crash the suite
                print(f"warning: failed to persist unsupported point {label}: {exc}", file=sys.stderr)
            points.append(SpeedPointResult(label, point, POINT_UNSUPPORTED))
            continue

        # Act 21: size the deterministic payload from the authoritative runtime token count so
        # actual input_tokens lands near the canonical target (8K/16K/32K) instead of ~68% of
        # it. The warmed calibration probe shares only a ~1-char prefix with the cold full-
        # prefill buster, so this cannot mask the genuine full-prefill TTFT (Act 20 preserved).
        try:
            filler = _filler_for_copies(
                await _speed_calibrated_filler_copies(rid, clean_model, base_url, point)
            )
        except Exception as exc:  # pragma: no cover - defensive; estimate path is always safe
            print(f"warning: speed calibration failed for {label}: {exc}", file=sys.stderr)
            filler = _filler_for_copies(_estimate_filler_copies(point))
        # Iteration 1 is the authoritative full-prefill sample; a deterministic, per-run,
        # per-point prefix at the START of its prompt defeats LM Studio's longest-common-
        # prefix KV-cache reuse so its TTFT is genuine full-prefill (not a cached hit).
        plain_payload = build_context_pressure_prompt(point, filler=filler)
        full_prefill_payload = _speed_cache_buster(rid, point) + plain_payload
        try:
            bench = await run_benchmark(
                lm_studio_url=base_url,
                model=clean_model,
                prompt=plain_payload,
                full_prefill_prompt=full_prefill_payload,
                iterations=SPEED_TOTAL_RUNS_PER_POINT,
                max_tokens=STANDARD_OUTPUT_TOKENS,
                temperature=0.0,
                hardware_label=hardware_label,
                execution_environment="Local",
                connection_type="",
                prompt_name=f"{label} ({point} tokens)",
                model_quantization=quantization,
            )
        except Exception as exc:  # pragma: no cover - defensive; one point must not abort the suite
            row = dict(identity)
            row.update({
                "iteration": SPEED_TOTAL_RUNS_PER_POINT,
                "cold_or_warm": "",
                "speed_run_stage": "",
                "tokens_per_second": 0,
                "ttft_seconds": None,
                "input_tokens": None,
                "output_tokens": None,
                "wall_time_seconds": 0,
                "prompt_name": f"{label} ({point} tokens) [failed]",
                "max_output_tokens": STANDARD_OUTPUT_TOKENS,
                "temperature": 0.0,
                "target_context_tokens": point,
                "context_point": label,
                "speed_point_status": POINT_FAILED,
            })
            try:
                store.add_run(row)
            except Exception:  # pragma: no cover - defensive
                pass
            points.append(SpeedPointResult(label, point, POINT_FAILED, error=str(exc)))
            continue

        # Persist one independent row per run (1 cold + 2 warm), then reuse the point-level
        # summary. Cold and warm are never averaged together: each run keeps its own telemetry,
        # prefill stays a cold-only signal, and generation uses the warm repeatability pair only.
        summary = _persist_speed_runs(store, identity, point, label, bench)
        points.append(SpeedPointResult(
            label, point, summary["status"],
            actual_prompt_tokens=summary["actual_prompt_tokens"],
            ttft_seconds=summary["ttft_seconds"],
            prefill_tokens_per_second=summary["prefill_tokens_per_second"],
            generation_tokens_per_second=summary["generation_tokens_per_second"],
            completion_tokens=summary["completion_tokens"],
            wall_time_seconds=summary["wall_time_seconds"],
        ))

    statuses = [p.status for p in points]
    if any(s == POINT_COMPLETED for s in statuses):
        overall_status = "completed"
    elif all(s == POINT_UNSUPPORTED for s in statuses):
        overall_status = "unsupported"
    else:
        overall_status = "failed"

    return {
        "status": overall_status,
        "speed_run_id": rid,
        "model": clean_model,
        "lm_studio_url": base_url,
        "points": [p.to_dict() for p in points],
    }


# ---------------------------------------------------------------------------
# Launch orchestration (separate OS process) + status -- execution plumbing only.
# ---------------------------------------------------------------------------

class SpeedLaunchError(Exception):
    """Pre-launch validation / concurrency failure (mapped to 4xx / 409 by the route).

    Carries a concise, human-safe message (never a stack trace or internal detail).
    """


# Project root -- identical to where ``python -m src v2-suite`` / ``v2-run`` spawn,
# so ``-m src`` resolves on ``sys.path`` inside the child process.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _speed_runs_root() -> Path:
    """Return the local log directory for speed runs (runtime data, never committed)."""
    return Path(__file__).resolve().parent.parent / "data" / "speed_runs"


def _resolve_lm_studio_url(lm_studio_url: Optional[str]) -> str:
    """Return a clean LM Studio base URL, falling back to the configured default."""
    if lm_studio_url and isinstance(lm_studio_url, str) and lm_studio_url.strip():
        return lm_studio_url.strip().rstrip("/")
    from src.config_loader import load_config

    return (load_config().get("lm_studio_url") or "http://localhost:1234").rstrip("/")


def launch_speed(
    model: Optional[str],
    lm_studio_url: Optional[str] = None,
    *,
    hardware_label: str = "",
    project_root: Optional[str] = None,
) -> dict[str, Any]:
    """Launch the standard Speed runner as a separate OS process and track it.

    Returns ``{"speed_run_id": ..., "status": "running"}``. Raises
    :class:`SpeedLaunchError` when the request is invalid (missing model) or when another
    standard run (Workflow *or* Speed) is already active -- two heavyweight suites must
    never contend for the same local model.

    The fixed contract (8K/16K/32K, iterations, output budget) lives entirely in the
    runner; this function exposes NO context-bin / iteration / output overrides.
    """
    if model is None or not isinstance(model, str) or not model.strip():
        raise SpeedLaunchError("A model must be selected to launch the Speed suite.")

    clean_model = model.strip()
    base_url = _resolve_lm_studio_url(lm_studio_url)

    # --- Single standard run at a time, enforced across BOTH suites via the shared guard. ---
    active = standard_run_guard.active_ids()
    if active:
        raise SpeedLaunchError(
            f"A standard benchmark is already in progress ({active[0]}). Wait for it to finish."
        )

    run_id = generate_speed_run_id()
    root = Path(project_root) if project_root else _PROJECT_ROOT

    # --- argv list only; no shell, no untrusted interpolation into command strings. ---
    cmd: list[str] = [
        sys.executable,
        "-m",
        "src",
        "v2-speed",
        "--model",
        clean_model,
        "--lm-studio-url",
        base_url,
        "--run-id",
        run_id,
    ]
    if hardware_label and str(hardware_label).strip():
        cmd += ["--hardware-label", str(hardware_label).strip()]

    # --- Stream child stdout/stderr to a bounded local log file. Never capture into memory.
    #     Logs live under data/ (runtime evidence), never under .pi/, and are not committed. ---
    runs_dir = _speed_runs_root()
    log_path = runs_dir / f"{run_id}.log"
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_path, "w", encoding="utf-8")
    except OSError:
        with contextlib.suppress(OSError):
            log_handle = open(os.devnull, "w", encoding="utf-8")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            env=dict(os.environ),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
    except BaseException as exc:  # pragma: no cover - defensive surface of launch errors
        with contextlib.suppress(OSError):
            log_handle.close()
        raise SpeedLaunchError(f"Failed to launch the speed process: {exc}")

    standard_run_guard.register(run_id, proc)
    return {"speed_run_id": run_id, "status": "running"}


def _store_rows_for(speed_run_id: str) -> list[dict[str, Any]]:
    """Return persisted speed rows for *speed_run_id* (empty when none)."""
    try:
        runs = _get_store().get_all()
    except Exception:  # pragma: no cover - defensive; missing store never crashes status
        return []
    return [r for r in runs if (r.get("run_id") or "") == speed_run_id]


def speed_status(speed_run_id: str) -> tuple[int, dict[str, Any]]:
    """Return ``(http_status, body)`` for a standard Speed run id.

    Resolution order mirrors the durability contract (one durable identity in the shared
    ResultsStore), reusing the same running/completed/failed semantics as Act 16:

    1. Durable result rows present (wins even if a stale handle lingers, e.g.
       after a FastAPI restart) -> ``200 completed`` (+ result_url)
    2. Live child handle still running with no data yet -> ``200 running``
    3. Child exited non-zero, no rows                   -> ``200 failed`` (safe reason)
    4. Clean exit but produced nothing                  -> ``200 failed``
    5. No live handle and no rows                       -> ``404 not found``
    6. Unsafe/malformed run id                          -> ``400 invalid run_id``

    Durable data is authoritative: a finished suite leaves durable result rows, so the
    status route reports ``completed`` even if an in-memory handle still looks alive.
    """
    # Reject unsafe ids before any filesystem interaction with them.
    try:
        _validate_speed_run_id(speed_run_id)
    except ValueError:
        return 400, {"error": "Invalid run_id"}

    rows = _store_rows_for(speed_run_id)
    if rows:
        # Durable result present: the correct deep-link for ONE Standard Speed run is
        # its dedicated page -- never the legacy shared /results aggregator.
        return 200, {
            "speed_run_id": speed_run_id,
            "status": "completed",
            "result_url": "/speed/results/" + speed_run_id,
        }

    active_ids = standard_run_guard.active_ids()  # also prunes exited handles
    if speed_run_id in active_ids:
        return 200, {"speed_run_id": speed_run_id, "status": "running"}

    code = standard_run_guard.exit_code(speed_run_id)
    if code is not None and code != 0:
        return 200, {
            "speed_run_id": speed_run_id,
            "status": "failed",
            "error": f"Speed process exited with status {code}; check the run log for details",
        }
    if code == 0:
        # Exited cleanly but produced no rows (should not normally happen).
        return 200, {
            "speed_run_id": speed_run_id,
            "status": "failed",
            "error": "Speed process exited without producing a result",
        }
    return 404, {"error": f"Speed run '{speed_run_id}' not found"}


def _validate_speed_run_id(run_id: Any) -> str:
    """Return a safe string ``run_id`` or raise ``ValueError`` (path-traversal guard)."""
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty string")
    if "\x00" in run_id or "/" in run_id or "\\" in run_id or os.sep in run_id:
        raise ValueError(f"invalid run_id (path traversal guard): {run_id!r}")
    return run_id
