"""Results routes for Solo Dev LLM Bench."""

from fastapi import APIRouter

import src.app_state

# Standard Speed read model (baseline contract only). Reused verbatim so the combined
# /results history page and the dedicated /speed/results/{run_id} page surface the SAME
# point-level values -- results.js never re-implements Speed semantics.
from src.v2_speed_read_model import (
    SpeedReadModelIntegrityError,
    SpeedRunNotFoundError,
    load_speed_run_by_id,
)

router = APIRouter()

# Canonical Standard Speed context points (authoritative for identification + ordering).
_STANDARD_SPEED_POINT_LABELS: tuple[str, ...] = ("8K", "16K", "32K")
_STANDARD_SPEED_TARGET_TOKENS: frozenset[int] = frozenset({8192, 16384, 32768})


def _get_results_store():
    """Get the current results_store from app_state module."""
    return src.app_state.results_store


@router.get("/api/results")
async def get_past_results():
    """Return normalized Standard Speed history for the /results page.

    The payload carries only ``speed_runs`` -- a normalized, point-level history of
    Standard Speed runs (the fixed 8K/16K/32K contract), built by reusing
    :func:`load_speed_run_by_id`. Each entry is the SAME authoritative data shown on the
    dedicated /speed/results/{run_id} page, so the combined history never blends 8K+16K+32K
    into one misleading run-wide tok/s and never invents a different average than the
    dedicated speed view. Legacy/custom benchmark runs are excluded from this surface; they
    render via their own dedicated result pages (e.g. /v2/results/{run_id} for Workflow).
    """
    results_store = _get_results_store()
    all_runs = results_store.get_all()

    # Sort by timestamp descending (newest first). Normalise None -> "" so rows that
    # lack a timestamp (e.g. standalone Speed runs) never crash the comparison view with
    # ``TypeError: '<' not supported between instances of 'NoneType' and 'NoneType'``.
    all_runs.sort(key=lambda r: (r.get("timestamp") or ""), reverse=True)

    return {
        "speed_runs": _normalize_standard_speed_history(all_runs),
    }


def _row_timestamp(row: dict) -> str:
    """Return a non-blank ISO timestamp, or '' when absent (standalone Speed runs)."""
    ts = row.get("timestamp")
    return ts if isinstance(ts, str) and ts.strip() else ""


def _is_standard_speed_candidate(rows: list[dict]) -> bool:
    """Pre-filter heuristic only; the read model is the authority.

    A run is a Standard Speed candidate when every row it carries targets one of the
    canonical 8K/16K/32K points (identified by ``context_point`` + ``target_context_tokens``,
    NOT by model name or DB row position). This avoids calling the read model on ordinary
    legacy/custom benchmark rows; any candidate that fails the strict contract is skipped.
    """
    if not rows:
        return False
    for r in rows:
        label = str((r.get("context_point") or "").strip())
        if label not in _STANDARD_SPEED_POINT_LABELS:
            return False
        try:
            if int(r.get("target_context_tokens")) not in _STANDARD_SPEED_TARGET_TOKENS:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _shape_speed_history_point(point: dict) -> dict:
    """Project the read-model point view into a compact history card field set.

    Uses ONLY baseline-safe keys returned by load_speed_run_by_id; calibration-only error
    fields are intentionally not surfaced here (not Act 22's concern). Missing telemetry
    stays None/blank and degrades to 'unavailable' in the UI -- never coerced to zero.
    """
    return {
        "label": point.get("label"),
        "target_context_tokens": point.get("target_context_tokens"),
        "actual_prompt_tokens": point.get("actual_prompt_tokens"),
        "ttft_seconds": point.get("ttft_seconds"),
        "prefill_tokens_per_second": point.get("prefill_tokens_per_second"),
        "generation_tokens_per_second": point.get("generation_tokens_per_second"),
        "completion_tokens": point.get("completion_tokens"),
        "wall_time_seconds": point.get("wall_time_seconds"),
    }


def _normalize_standard_speed_history(all_runs: list[dict]) -> list[dict]:
    """Return normalized Standard Speed run summaries in newest-first order.

    Reuses load_speed_run_by_id() so history grouping, canonical point ordering and metric
    semantics are identical to the dedicated speed page. Runs that fail the strict contract
    (foreign rows, duplicate points, divergent model) raise there and are skipped rather than
    crashing the history list -- they fall back to the legacy rendering path.
    """
    by_run: dict[str, list[dict]] = {}
    for run in all_runs:
        if not isinstance(run, dict):
            continue
        rid = str(run.get("run_id") or "")
        if not rid:
            continue
        by_run.setdefault(rid, []).append(run)

    speed_runs: list[dict] = []
    for rid, rows in by_run.items():
        # Cheap pre-filter; the read model remains the authority via try/except below.
        if not _is_standard_speed_candidate(rows):
            continue
        try:
            norm = load_speed_run_by_id(rid, all_runs)
        except (SpeedRunNotFoundError, SpeedReadModelIntegrityError):
            # Not a well-formed Standard Speed run after all -- leave it for legacy rendering.
            continue

        # Newest timestamp among the run's rows (None -> '' degrades to '—' in the UI).
        ts = max((_row_timestamp(r) for r in rows), key=lambda t: (t != "", t))
        display_name = ""
        hw = ""
        env = ""
        conn = ""
        for r in rows:
            dn = r.get("model_display_name") or r.get("model_key") or ""
            if dn:
                display_name = dn
            hw = hw or str(r.get("hardware_label") or "")
            env = env or str(r.get("execution_environment") or "")
            conn = conn or str(r.get("connection_type") or "")

        speed_runs.append({
            "run_id": rid,
            "type": "standard_speed",
            "model_identifier": norm.get("model_identifier") or "",
            "model_display_name": display_name,
            # Filter-support fields (mirror the flat row path so the shared filter predicate
            # behaves identically for Standard Speed runs and legacy/custom runs).
            "hardware_label": hw,
            "execution_environment": env,
            "connection_type": conn,
            "timestamp": ts,
            "metric_version": norm.get("speed_metric_version"),
            "legacy_prefill_warning": bool(norm.get("legacy_prefill_warning")),
            "points": [_shape_speed_history_point(p) for p in (norm.get("points") or [])],
        })

    # Newest-first, matching the ordering of ``results``.
    speed_runs.sort(key=lambda s: s.get("timestamp") or "", reverse=True)
    return speed_runs

