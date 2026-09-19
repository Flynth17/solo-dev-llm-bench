"""Read-only adapter: expose persisted *dedicated speed/performance benchmark* runs
associated with a V2 quality result (UI Act 13).

This is a **pure view-model / adapter layer only** -- it performs NO benchmark logic:

* it never executes a benchmark, never loads an LM Studio model and never recomputes
  any measurement;
* it reads ONLY already-persisted dedicated speed rows from the ``ResultsStore``
  (SQLite/CSV), the shared persistence used across suites to durably store runs; it no
  longer shares a route with the retired generic benchmark product.
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


# ---------------------------------------------------------------------------
# Single Standard Speed run read model (Act 19.1)
#
# A dedicated, self-contained view over ONE persisted Standard Speed run
# (speed-<id>, fixed 8K / 16K / 32K contract). It is intentionally decoupled from
# the Workflow quality result: it never calls load_v2_result(), never matches on
# model/context compatibility and never touches legacy small/medium/large rows.
# The ONLY join key is an EXACT speed_run_id match, so a Speed-only run is
# reconstructable without any companion artifact.
# ---------------------------------------------------------------------------

# Canonical Standard Speed contract: target context tokens -> user-facing label.
# Ordering here is authoritative for rendering; DB row order must never decide it.
STANDARD_SPEED_CANONICAL_POINTS: dict[int, str] = {
    8192: "8K",
    16384: "16K",
    32768: "32K",
}


class SpeedRunNotFoundError(ValueError):
    """Raised when no persisted Standard Speed row exists for the run id."""


class SpeedReadModelIntegrityError(RuntimeError):
    """Raised when a run's rows violate the Standard Speed contract.

    Covers non-standard/foreign rows tagged to this run, duplicate canonical points,
    divergent model identity across rows and target points outside the 8K/16K/32K
    contract. These are server-owned data problems mapped to HTTP 500 by the route.
    """


def _as_number(value: Any) -> Optional[float]:
    """Return a finite float for *value*, or None when absent / non-numeric / falsey."""
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    # Guard against NaN / inf leaking into the UI.
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return num


def _project_speed_point(row: dict) -> dict[str, Any]:
    """Project one persisted Standard Speed row into the normalized point view.

    Values are preserved verbatim -- prefill is NOT recomputed or "corrected" here
    (semantics under review in a later Act). Missing telemetry becomes None so the UI
    can render "Not recorded"; it is never coerced to zero.
    """
    status_raw = str((row.get("speed_point_status") or "").strip().lower()) if row.get("speed_point_status") else ""
    state_raw = str((row.get("cold_or_warm") or "").strip().lower()) if row.get("cold_or_warm") else ""

    # Version >= 2 (corrected prefill, Act 20) and version >= 3 (calibrated targets, Act 21)
    # are both non-legacy: neither is a cached-TTFT artifact. Legacy (missing/``None`` or 1)
    # rows still carry the warning; never fabricate a version historical rows never had.
    _metric_version = _norm_int(row.get("speed_metric_version"))
    # Act 21 derived error fields are NULL-safe: a missing/unsupported point has no input
    # tokens, so target-error evidence is None there (never an arithmetic TypeError).
    _actual = _norm_int(row.get("input_tokens"))
    _target = _norm_int(row.get("target_context_tokens"))

    return {
        "label": STANDARD_SPEED_CANONICAL_POINTS.get(
            _norm_int(row.get("target_context_tokens")), "?"
        ),
        "target_context_tokens": _norm_int(row.get("target_context_tokens")),
        "actual_prompt_tokens": _norm_int(row.get("input_tokens")),
        "ttft_seconds": _as_number(row.get("ttft_seconds")),
        "prefill_tokens_per_second": _as_number(row.get("prefill_tokens_per_second")),
        "generation_tokens_per_second": _as_number(row.get("tokens_per_second")),
        "completion_tokens": _norm_int(row.get("output_tokens")),
        "wall_time_seconds": _as_number(row.get("wall_time_seconds")),
        # state/status kept verbatim so the UI renders them honestly.
        "state": state_raw or None,
        "status": status_raw or "completed",
        # Metric version + legacy warning (Act 20/21). A missing/legacy row carries no metric
        # version; runs at or above the corrected-metric baseline (>= 2) are exempt from the
        # cached-TTFT prefill warning -- neither version 2 (corrected prefill) nor version 3
        # (calibrated targets) is a cached-TTFT artifact. Never fabricate a version historical
        # rows never had.
        "speed_metric_version": _metric_version,
        "legacy_prefill_warning": _metric_version is None or _metric_version < 2,
        # Act 21: target-error evidence, derived from already-persisted columns (no schema
        # churn). Lets the result page show that calibration landed near the canonical target.
        "target_error_tokens": (_actual - _target) if _actual is not None else None,
        "target_error_percent": (
            round(100.0 * (_actual - _target) / _target, 2)
            if _actual is not None and _target
            else None
        ),
    }


# --- Repeatability (Evidence-First Redesign): independent runs per point -------------
#
# Each SUPPORTED point persists one row per stage -- 1 cold (cache-busted full-prefill) +
# 2 warm (warm_a / warm_b) -- tagged by speed_run_stage. The helpers below let the read
# model relax its old "one aggregate row per point" validation, surface each run's explicit
# provenance/validity under ``runs``, and derive the flat point summary without ever mixing
# cold and warm: prefill stays a cold-only signal and generation is a warm-only mean.
#
# Legacy single-row-per-point rows (no speed_run_stage) keep their exact historical shape so
# old evidence renders unchanged -- only when more than one stage is persisted for a point is
# the extra ``runs`` provenance list attached.


def _stage_of(row: dict) -> str:
    """Return a row's repeatability stage tag (empty string for legacy/unstaged rows)."""
    raw = row.get("speed_run_stage")
    if isinstance(raw, str):
        return raw.strip().lower() or ""
    return ""


def _project_stage_run(row: dict) -> dict[str, Any]:
    """Project one persisted stage row into a full per-run evidence view.

    Extends the flat point projection with explicit provenance (run_id + speed_run_stage),
    the warm-run raw TTFT kept as diagnostic evidence, optional reasoning-token capture,
    and status/state verbatim -- so downstream UIs can render successful runs by default and
    unsuccessful ones behind an explicit toggle. Missing telemetry stays None (NOT REPORTED).
    """
    proj = dict(_project_speed_point(row))
    proj["speed_run_stage"] = _stage_of(row)
    # Explicit validity + provenance verbatim (computed ``status`` is derived); a failed or
    # interrupted stage stays visible under this flag so the UI can render successful runs by
    # default and unsuccessful ones behind an explicit Show-unsuccessful toggle.
    proj["speed_point_status"] = (
        str((row.get("speed_point_status") or "").strip().lower()) or "completed"
    )
    reasoning = row.get("reasoning_output_tokens")
    proj["reasoning_output_tokens"] = (
        reasoning if isinstance(reasoning, int) and not isinstance(reasoning, bool) else None
    )
    warm_ttft = row.get("warm_ttft_seconds")
    proj["warm_ttft_seconds"] = _as_number(warm_ttft)
    proj["run_id"] = _norm_str(row.get("run_id"))
    return proj


def _representative_speed_run(rows: list) -> dict:
    """Pick the row that drives a point's flat summary.

    The cold full-prefill run is preferred (it owns prefill/ttft/actual_prompt_tokens);
    otherwise the first completed run; otherwise the first persisted row. This keeps the flat
    summary anchored on genuine full-prefill evidence rather than a cache-reused warm sample,
    and preserves legacy single-row behavior.
    """
    for r in rows:
        if _stage_of(r) == "cold":
            return r
    for r in rows:
        status = str((r.get("speed_point_status") or "").strip().lower())
        if status == "completed":
            return r
    return rows[0]


def _ordered_runs(rows: list) -> list:
    """Order a point's runs into the canonical cold -> warm_a -> warm_b sequence."""
    order = {"cold": 0, "warm_a": 1, "warm_b": 2}
    return sorted(rows, key=lambda r: order.get(_stage_of(r), len(order)))


def _has_warm_stages(rows: list) -> bool:
    """True when the point persists at least one warm repeatability-stage run."""
    return any(_stage_of(r) in ("warm_a", "warm_b") for r in rows)


def _warm_only_mean(rows: list) -> Optional[float]:
    """Warm-only mean of completed runs' generation throughput; cold decode excluded.

    Never blends cold and warm. Returns None when no warm run completed so a missing value is
    honest rather than a fabricated zero (falls back to the representative's own value in that
    case, keeping legacy single-row data unchanged).
    """
    values: list[float] = []
    for r in rows:
        if _stage_of(r) not in ("warm_a", "warm_b"):
            continue
        if str((r.get("speed_point_status") or "").strip().lower()) != "completed":
            continue
        tps = _as_number(r.get("tokens_per_second"))
        if isinstance(tps, (int, float)) and tps > 0:
            values.append(float(tps))
    return round(sum(values) / len(values), 2) if values else None


def _point_status(rows: list) -> str:
    """Point-level status over all persisted stages.

    Completed only when every stage is completed; an explicit single non-completed state (e.g.
    ``unsupported``) is preserved verbatim so it is not misreported as partial; any mix of
    successful and unsuccessful stages yields ``partial`` so unsuccessful runs remain visible as
    diagnostic evidence behind the Show-unsuccessful toggle rather than being collapsed away.
    """
    statuses = [str((r.get("speed_point_status") or "").strip().lower()) or "completed" for r in rows]
    if all(s == "completed" for s in statuses):
        return "completed"
    distinct = {s for s in statuses}
    if len(distinct) == 1:
        return next(iter(distinct))
    return "partial"


def load_speed_run_by_id(run_id: str, runs: Iterable[Any]) -> dict[str, Any]:
    """Return the normalized Standard Speed result for exactly one speed run id.

    ``run_id`` -- the durable ``speed-<id>`` (matched EXACTLY).
    ``runs``  -- iterable of persisted rows (e.g. ResultsStore.get_all()).

    Raises:
        SpeedRunNotFoundError: no row persists under this run id.
        SpeedReadModelIntegrityError: the run mixes non-standard / duplicate / foreign
            rows, divergent model identity or a target outside the contract.
    """
    rid = _norm_str(run_id) if isinstance(run_id, str) else None
    if not rid:
        raise SpeedRunNotFoundError("run id must be a non-empty string")

    run_rows = [r for r in (runs or []) if isinstance(r, dict)]
    mine = [r for r in run_rows if (r.get("run_id") or "") == rid]

    if not mine:
        raise SpeedRunNotFoundError(f"no Standard Speed run persisted for {rid!r}")

    # --- Contract validation over the run's own rows. -----------------------
    model_ids: set[str] = set()
    seen_targets: dict[Optional[int], list[dict]] = {}

    for r in mine:
        target = _norm_int(r.get("target_context_tokens"))

        # Every row tagged to a Standard Speed run must itself be a standard point.
        if target is None or target not in STANDARD_SPEED_CANONICAL_POINTS:
            raise SpeedReadModelIntegrityError(
                f"run {rid!r} contains a row outside the 8K/16K/32K Standard Speed contract"
            )

        seen_targets.setdefault(target, []).append(r)

        model_id = _norm_str(r.get("model_key")) or _norm_str(r.get("model_display_name"))
        if model_id:
            model_ids.add(model_id)

    # Repeatability contract: each supported point persists one independent row per stage --
    # 1 cold + 2 warm (warm_a / warm_b) -- tagged by speed_run_stage. Multiple DISTINCT stages
    # for the same target are expected and representable; genuine DUPLICATES (the same stage,
    # or more than one unstaged legacy row, appearing twice for one target) indicate corrupt/
    # mixed rows and are rejected rather than silently collapsed.
    seen_stages: dict[Optional[int], set[str]] = {}
    for target, rows in seen_targets.items():
        stages = seen_stages.setdefault(target, set())
        for r in rows:
            stage = _stage_of(r)
            if stage in stages:
                raise SpeedReadModelIntegrityError(
                    f"run {rid!r} has duplicate rows for the "
                    f"{STANDARD_SPEED_CANONICAL_POINTS[target]} point at stage {stage!r}"
                )
            stages.add(stage)

    # Model identity must be consistent across every row in the run.
    if len(model_ids) > 1:
        raise SpeedReadModelIntegrityError(
            f"run {rid!r} mixes divergent model identities: {sorted(model_ids)}"
        )

    # --- Build the canonical, ordered point list. --------------------------
    # Each supported point persists one independent row per stage (1 cold + 2 warm). The flat
    # point summary is projected from the representative stage (cold full-prefill first) so
    # prefill/ttft stay a cold-only signal and generation uses a warm-only mean -- never an
    # average that blends cold and warm. Every persisted stage is additionally exposed under
    # ``runs`` (with speed_run_stage, telemetry and validity) so downstream UIs can show
    # successful runs by default and unsuccessful ones behind an explicit toggle. Legacy
    # single-row-per-point rows keep the exact historical flat shape (no ``runs`` key).
    points: list[dict[str, Any]] = []
    statuses: set[str] = set()
    for target in (8192, 16384, 32768):
        rows = seen_targets.get(target)
        if not rows:
            # A missing canonical point is left out rather than fabricated; the
            # remaining points still render. Unsupported-but-stored points are kept.
            continue
        representative = _representative_speed_run(rows)
        point = dict(_project_speed_point(representative))
        # Flat generation is a warm-only mean of the repeatability pair (cold excluded); never
        # blended with cold decode. Falls back to the representative value when no warm run
        # completed, keeping legacy single-row data unchanged.
        point["generation_tokens_per_second"] = (
            _warm_only_mean(rows) if _has_warm_stages(rows)
            else point.get("generation_tokens_per_second")
        )
        point["status"] = _point_status(rows)
        statuses.add(point.get("status") or "completed")
        # Provenance + validity per stage (only when more than one run is persisted for the point).
        runs = [_project_stage_run(r) for r in _ordered_runs(rows)]
        if len(runs) > 1:
            point["runs"] = runs
        points.append(point)

    if not points:
        raise SpeedRunNotFoundError(f"no renderable Standard Speed point for {rid!r}")

    # --- Overall status (representable, never crashes the read). ------------
    if statuses <= {"completed"}:
        overall = "completed"
    elif statuses <= {"completed", "unsupported"}:
        overall = "partial"
    else:
        overall = "unknown"

    # --- Configuration identity from first-class fields (never fabricated). --
    first = mine[0]
    configuration: dict[str, Any] = {}
    for key, src in (
        ("loaded_context", "loaded_context"),
        ("model_max_context", "model_max_context"),
        ("hardware_label", "hardware_label"),
        ("execution_environment", "execution_environment"),
        ("connection_type", "connection_type"),
        ("max_output_tokens", "max_output_tokens"),
        ("reasoning_mode", "reasoning_mode"),
    ):
        value = _norm_int(first.get(src)) if key in ("loaded_context", "model_max_context") \
            else _norm_str(first.get(src))
        # Always surface the two context keys (honest even when None); optional blanks omitted.
        if key in ("loaded_context", "model_max_context") or value is not None:
            configuration[key] = value

    model_identifier = next((m for m in model_ids), _norm_str(first.get("model_key")))

    # Act 20: a run is 'legacy' when any point was measured with the pre-fix cached-TTFT
    # prefill semantics. Corrected runs (all points at metric version 2) show no warning.
    legacy_prefill_warning = any(
        bool(p.get("legacy_prefill_warning")) for p in points
    )

    # Act 21: run-level calibration summary derived from per-point error fields (no schema
    # churn). Worst-case absolute target error (% of target) and mean signed error tokens
    # across points that carry input data -- evidence the x-axis was calibrated on.
    _err_pcts = [
        abs(p.get("target_error_percent") or 0.0)
        for p in points
        if p.get("target_error_percent") is not None
    ]
    _abs_err_pct = max(_err_pcts) if _err_pcts else None
    _toks = [
        p.get("target_error_tokens")
        for p in points
        if p.get("target_error_tokens") is not None
    ]
    _mean_err_tokens = round(sum(_toks) / len(_toks), 1) if _toks else None

    return {
        "run_id": rid,
        "model_identifier": model_identifier,
        "status": overall,
        "configuration": configuration,
        "points": points,
        "speed_metric_version": next(
            (p["speed_metric_version"] for p in points if p.get("speed_metric_version") is not None),
            None,
        ),
        "legacy_prefill_warning": legacy_prefill_warning,
        "max_abs_target_error_percent": _abs_err_pct,
        "mean_target_error_tokens": _mean_err_tokens,
    }
