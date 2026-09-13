"""Read-only adapter: expose persisted *dedicated speed/performance benchmark* runs
associated with a V2 quality result (UI Act 13).

This is a **pure view-model / adapter layer only** -- it performs NO benchmark logic:

* it never executes a benchmark, never loads an LM Studio model and never recomputes
  any measurement;
* it reads ONLY already-persisted dedicated speed rows from the ``ResultsStore``
  (SQLite/CSV) -- the same persistence that ``src.routes.benchmark`` appends to;
* it associates speed data with a quality result by **explicit identity only** and
  never by assuming the two systems' configuration fingerprints are equivalent.

Why not fingerprints? The benchmark store derives its own ``configuration_fingerprint``
from ``results.py::compute_configuration_fingerprint`` (keyed on model + quantization +
loaded context + runtime settings), which is a *different construction* from the V2
quality artifact's ``configuration_fingerprint``. They are unrelated schemas, so treating
matching fingerprints as proof of "same configuration" would be an invented equivalence
that could silently join incompatible runs. We instead match on fields genuinely exposed
by BOTH systems as first-class identity/config values:

* base model -- ``model_identifier`` (quality) vs ``model_key`` (speed run);
* loaded context window -- ``loaded_context`` (both), compared only when *both* carry it.

Quantization is **intentionally not** compared: the V2 quality result does not record a
weight quantization, so comparing one would require an invented field and could silently
match a same-name/different-quantisation run. A match therefore never claims more than
"same base model (+ same loaded context)"; it is always labelled as such.

If no compatible row exists the result reports ``none`` (no persisted speed data at all)
or ``incompatible`` (speed runs exist but none share the identity). Neither is chosen
silently -- the status + basis are returned explicitly for the UI to display.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional


# --- small type guards -------------------------------------------------------------

def _norm_int(value: Any) -> Optional[int]:
    """Return a positive int, or None when absent/zero/negative/non-inteferable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return None
    return iv if iv > 0 else None


def _norm_str(value: Any) -> Optional[str]:
    """Return a non-blank stripped string, or None."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# --- metric projection -------------------------------------------------------------

def _project_row(row: dict) -> dict:
    """Project one persisted dedicated speed row into a display-ready shape.

    Only recorded values are surfaced; absence stays ``None`` (never coerced to zero).
    Prefill rate is derived from TWO recorded quantities (prompt/context tokens divided
    by TTFT) -- it is physically *what* prefill throughput means and is never the
    generation value, but only when both inputs are present and positive.
    """
    ctx = _norm_int(row.get("input_tokens"))          # prompt/context tokens for this bin
    ttft = row.get("ttft_seconds")
    gen = row.get("tokens_per_second")                # generation / decode throughput (recorded)

    prefill: Optional[float] = None
    if isinstance(ttft, (int, float)) and not isinstance(ttft, bool) and ttft > 0 and ctx:
        try:
            prefill = round(float(ctx) / float(ttft), 1)
        except (TypeError, ValueError):
            prefill = None

    state_raw = row.get("cold_or_warm")
    run_state = str(state_raw).lower() if _norm_str(state_raw) else None

    return {
        "context_tokens": ctx,
        "ttft_seconds": ttft if isinstance(ttft, (int, float)) and not isinstance(ttft, bool) else None,
        "prefill_tokens_per_second": prefill,
        "generation_tokens_per_second": gen if isinstance(gen, (int, float)) and not isinstance(gen, bool) else None,
        "run_state": run_state,
    }


# --- primary entry point -----------------------------------------------------------

def load_speed_result(quality_identity: dict, runs: Iterable[Any]) -> dict[str, Any]:
    """Return the dedicated-speed association view for a V2 quality result.

    ``quality_identity`` -- minimal identity drawn from the quality read model::

        {"model_identifier": <str|None>, "loaded_context": <int|None>}

    ``runs`` -- iterable of persisted dedicated speed rows (e.g. ResultsStore.get_all()).
    Nothing here is executed or recomputed; existing rows are only read and reshaped.
    """
    q_model = _norm_str(quality_identity.get("model_identifier")) if isinstance(
        quality_identity, dict) else None
    q_ctx = _norm_int(quality_identity.get("loaded_context")) if isinstance(
        quality_identity, dict) else None

    run_list = [r for r in (runs or []) if isinstance(r, dict)]

    # No dedicated speed data persisted at all -> honest "none" status.
    if not run_list:
        return {
            "compatibility": {
                "status": "none",
                "basis": "no dedicated speed benchmark data is persisted for this server",
            },
            "model_identifier": q_model,
            "speed_run_ids": [],
            "rows": [],
        }

    # Explicit identity match. loaded context is a hard equality guard only when BOTH
    # sides carry it; otherwise model match alone proceeds (still explicitly labelled).
    matches: list[dict] = []
    for r in run_list:
        r_model = _norm_str(r.get("model_key"))
        if not r_model or r_model != q_model:
            continue
        r_ctx = _norm_int(r.get("loaded_context"))
        # When the quality side knows its loaded-context window, require an equal one on
        # the run: this prevents silently joining a same-name/different-window (or a
        # different-quantisation run that omitted context) row. When quality supplies no
        # context, fall back to an explicit base-model-only match (labelled 'weak').
        if q_ctx is not None and (r_ctx is None or r_ctx != q_ctx):
            continue
        matches.append(r)

    def _basis() -> str:
        return "model + loaded context" if q_ctx is not None else "base model"

    if not matches:
        detail = ("no persisted speed benchmark found for base model {!r}".format(q_model))
        if q_ctx is not None:
            detail += " with loaded context {}".format(q_ctx)
        return {
            "compatibility": {
                "status": "incompatible",
                "basis": detail,
            },
            "model_identifier": q_model,
            "speed_run_ids": [],
            "rows": [],
        }

    rows = [_project_row(r) for r in matches]
    # Sort numerically by context size; rows with unknown context sink to the end.
    rows.sort(key=lambda row: (row["context_tokens"] is None, row["context_tokens"] or 0))
    speed_run_ids = sorted({(r.get("run_id") or "") for r in matches if r.get("run_id")})

    return {
        "compatibility": {"status": "compatible", "basis": _basis()},
        "model_identifier": q_model,
        "speed_run_ids": speed_run_ids,
        "rows": rows,
    }
