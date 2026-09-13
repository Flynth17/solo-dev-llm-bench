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
from src.evaluation_prompts import estimate_tokens

# Shared cross-suite concurrency guard (Act 17). Launch registers the child handle here
# so Workflow and Speed reject overlapping launches bidirectionally.
import src.standard_run_guard as standard_run_guard  # noqa: E402

# Canonical benchmark reasoning rule: throughput metrics are measured with reasoning
# explicitly OFF (identical to run_benchmark's payload and the legacy speed path).
from src.results import BENCHMARK_REASONING_MODE  # noqa: E402


# ---------------------------------------------------------------------------
# Standard Speed contract constants.
# ---------------------------------------------------------------------------

# Binary context bins -- NOT decimal 8,000/16,000/32,000. These are the authoritative
# standard points and are never derived from validator output or user input.
CANONICAL_CONTEXT_POINTS: tuple[int, ...] = (8192, 16384, 32768)

# Human-facing labels for each canonical point.
CONTEXT_POINT_LABELS: dict[int, str] = {8192: "8K", 16384: "16K", 32768: "32K"}

# Fixed iteration count shared by every point (never varies between context points).
# Reuses the project's established benchmark/evaluation default.
STANDARD_ITERATIONS: int = 5

# Fixed generation/target output budget for stable decode throughput measurement,
# identical across all points and models/configurations. Deliberately NOT inherited
# from the Workflow Suite's 75_percent_context policy -- Speed and Workflow differ.
STANDARD_OUTPUT_TOKENS: int = 512

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


def build_context_pressure_prompt(target_tokens: int) -> str:
    """Return a deterministic, content-insensitive payload targeting *target_tokens*.

    The filler is built by appending whole copies of a fixed sentence until an
    internal token estimate reaches (or just passes) the target. No randomness, no
    network calls, no quality challenge -- pure reproducible input pressure. Actual
    loaded prompt tokens are authoritative from the runtime and recorded separately, so
    a small estimation tolerance is expected and never misreported as exact.
    """
    header = f"[standard-speed {target_tokens} token context-pressure point]\n"
    blocks: list[str] = []
    est = estimate_tokens(header)
    block_estimate = estimate_tokens(_PADDING_SENTENCE)
    while est < target_tokens:
        blocks.append(_PADDING_SENTENCE)
        est += block_estimate
    return header + "".join(blocks)


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


def _aggregate_point(bench: dict[str, Any]) -> dict[str, Any]:
    """Collapse a :func:`run_benchmark` result into one representative point snapshot.

    Generation throughput uses the warm-iteration average when available (iteration 1 is
    ``cold`` and includes model-load variance), falling back to the all-iteration average.
    TTFT mirrors that choice. Actual prompt tokens are constant across iterations (same
    deterministic payload) so the first iteration's value is representative; completion
    tokens use the final iteration as a representative generation length.
    """
    runs = bench.get("runs", []) or []
    warm = bench.get("warm_aggregate", {}) or {}
    overall = bench.get("aggregate", {}) or {}

    tps = warm.get("avg_tokens_per_second")
    if not isinstance(tps, (int, float)) or tps <= 0:
        tps = overall.get("avg_tokens_per_second")

    ttft = warm.get("avg_ttft")
    if not isinstance(ttft, (int, float)):
        ttft = runs[0].get("ttft_seconds") if runs else 0

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

    points: list[SpeedPointResult] = []
    for point in CANONICAL_CONTEXT_POINTS:
        label = CONTEXT_POINT_LABELS[point]
        if not _capacity_supports(effective_capacity, point):
            # Effective/loaded context is below the target -- mark unsupported explicitly
            # and persist that status; never silently shrink the target.
            row = dict(identity)
            row.update({
                "iteration": STANDARD_ITERATIONS,
                "cold_or_warm": "",
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

        payload = build_context_pressure_prompt(point)
        try:
            bench = await run_benchmark(
                lm_studio_url=base_url,
                model=clean_model,
                prompt=payload,
                iterations=STANDARD_ITERATIONS,
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
                "iteration": STANDARD_ITERATIONS,
                "cold_or_warm": "",
                "tokens_per_second": 0,
                "ttft_seconds": None,
                "input_tokens": None,
                "output_tokens": None,
                "wall_time_seconds": 0,
                "prompt_name": f"{label} ({point} tokens)",
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

        agg = _aggregate_point(bench)
        row = dict(identity)
        row.update({
            "iteration": STANDARD_ITERATIONS,
            "cold_or_warm": "warm",  # warm-throughput representative (model stays loaded across points)
            "tokens_per_second": agg["generation_tokens_per_second"],
            "ttft_seconds": agg["ttft_seconds"],
            "input_tokens": agg["actual_prompt_tokens"],
            "prefill_tokens_per_second": agg["prefill_tokens_per_second"],
            "output_tokens": agg["completion_tokens"],
            "wall_time_seconds": agg["wall_time_seconds"],
            "prompt_name": f"{label} ({point} tokens)",
            "max_output_tokens": STANDARD_OUTPUT_TOKENS,
            "temperature": 0.0,
            "target_context_tokens": point,
            "context_point": label,
            "speed_point_status": POINT_COMPLETED,
        })
        try:
            store.add_run(row)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"warning: failed to persist point {label}: {exc}", file=sys.stderr)
        points.append(SpeedPointResult(
            label, point, POINT_COMPLETED,
            actual_prompt_tokens=agg["actual_prompt_tokens"],
            ttft_seconds=agg["ttft_seconds"],
            prefill_tokens_per_second=agg["prefill_tokens_per_second"],
            generation_tokens_per_second=agg["generation_tokens_per_second"],
            completion_tokens=agg["completion_tokens"],
            wall_time_seconds=agg["wall_time_seconds"],
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
