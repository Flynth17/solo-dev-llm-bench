"""Comparison API routes for RM-26-AA-0013 (ST-002).

Read-only endpoints that expose the two-subject comparison selection flow defined by
:mod:`src.comparison_read_model`. The route layer is responsible *only* for
enumerating committed authoritative evidence and loading each artifact exactly once;
all alignment, run-selection and state logic lives in the pure composition layer.

Routes::

    GET /api/comparison/subjects   -> catalogue of selectable comparison subjects
    GET /api/comparison?a=<key>&b=<key> -> per-dimension comparison projection

No benchmark scoring or dimension recomputation happens here (architecture §3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

import src  # noqa: F401  (ensures app_state is importable)
from src.comparison_read_model import (
    FAMILIES,
    build_subject_catalogue,
    project_speed_point,
    resolve_comparison,
)
from src.v2_speed_read_model import (
    SpeedReadModelIntegrityError,
    SpeedRunNotFoundError,
    load_speed_run_by_id,
)

router = APIRouter(prefix="/api/comparison", tags=["comparison"])


def _normalize_speed_candidate(
    run_id: str,
    all_rows: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> tuple[Optional[list[dict[str, Any]]], Optional[int]]:
    """Attach the authoritative point-level view for one Speed run (ST-004).

    Reuses :func:`load_speed_run_by_id` so the comparison surfaces the SAME values as the
    dedicated /speed/results/{run_id} page -- no second normalization path. Runs outside
    the Standard Speed contract (or in a shape the single-run read model cannot represent)
    yield no points: the read model then reports an honest non-comparable state instead of
    fabricating values. The run-level metric version falls back to the first persisted
    per-row version so legacy evidence is still labelled honestly when normalization is
    unavailable.
    """
    try:
        view = load_speed_run_by_id(run_id, all_rows)
        points = [project_speed_point(p) for p in (view.get("points") or [])]
        return points, view.get("speed_metric_version")
    except (SpeedRunNotFoundError, SpeedReadModelIntegrityError):
        version: Optional[int] = None
        for row in rows:
            v = row.get("speed_metric_version") if isinstance(row, dict) else None
            if isinstance(v, int) and not isinstance(v, bool):
                version = v
                break
        return None, version


def _build_candidates() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Enumerate committed authoritative evidence into comparison candidates.

    Returns ``(speed_runs, workflow_candidates, context_candidates)`` in the shape
    :mod:`src.comparison_read_model` expects. Each artifact is loaded exactly once;
    artifacts that fail to parse or validate are skipped (they are not valid
    evidence for a finished comparison and remain inspectable via their deep link).
    """
    from src.results import classify_run_for_result
    from src.v2_context_artifact import load as _load_context
    from src.v2_context_artifact import CONTEXT_RUNS_DIR
    from src.v2_context_read_model import build_read_model as _build_context_view
    from src.v2_quality_artifact import V2_RUNS_DIR, load as _load_workflow
    from src.v2_quality_read_model import build_read_model as _build_workflow_view

    # --- Speed: group persisted point rows back into per-run candidates. --------
    all_rows = src.app_state.results_store.get_all()
    speed_runs: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in all_rows:
        run_id = (row or {}).get("run_id")
        if not run_id:
            continue
        grouped.setdefault(run_id, []).append(row)
    # Deterministic order so the catalogue / selection are reproducible.
    for run_id in sorted(grouped):
        rows = grouped[run_id]
        points, metric_version = _normalize_speed_candidate(run_id, all_rows, rows)
        speed_runs.append(
            {
                "family": "speed",
                "run_id": run_id,
                "rows": rows,
                # Persisted Speed runs have finished; classification is best-effort
                # provenance (canonical/incomplete). Status stays terminal.
                "classification": classify_run_for_result(rows[0]),
                "status": "completed",
                # ST-004: normalized canonical points + run-level metric version from the
                # authoritative single-run read model. ``points`` is None when the run is
                # not representable under the Standard Speed contract -- never fabricated.
                "points": points,
                "metric_version": metric_version,
            }
        )

    # --- Workflow / Context: load each persisted artifact exactly once. ----------
    def _family_candidates(
        runs_dir: Path, loader, build_view, family: str
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        if not runs_dir.exists():
            return candidates
        for path in sorted(runs_dir.glob("*.json")):
            run_id = path.stem
            try:
                document = loader(run_id)
                view = build_view(document)
            except Exception:
                # Malformed / unsupported-schema / integrity-invalid artifact.
                # Skip from finished comparison; still reachable via deep link.
                continue
            candidates.append(
                {
                    "family": family,
                    "run_id": document.get("run_id") or run_id,
                    "generated_at": document.get("generated_at"),
                    "view": view,
                }
            )
        return candidates

    workflow_candidates = _family_candidates(
        V2_RUNS_DIR, _load_workflow, _build_workflow_view, "workflow"
    )
    context_candidates = _family_candidates(
        CONTEXT_RUNS_DIR, _load_context,
        _build_context_view,
        "context",
    )
    return speed_runs, workflow_candidates, context_candidates


@router.get("/subjects")
def comparison_subjects_endpoint() -> dict[str, Any]:
    """Catalogue of selectable comparison subjects (read-only)."""
    speed_runs, workflow_candidates, context_candidates = _build_candidates()
    return build_subject_catalogue(speed_runs, workflow_candidates, context_candidates)


@router.get("")
def comparison_endpoint(a: Optional[str] = None, b: Optional[str] = None) -> dict[str, Any]:
    """Project a two-subject comparison (read-only).

    Query parameters ``a`` and ``b`` are subject keys from the catalogue endpoint.
    Rejects identical subjects with 400; unknown/invalid keys surface as 400 so the
    frontend never guesses at identity.
    """
    if not a or not b:
        raise HTTPException(status_code=400, detail="both 'a' and 'b' subject keys are required")

    try:
        speed_runs, workflow_candidates, context_candidates = _build_candidates()
        return resolve_comparison(
            a, b, speed_runs, workflow_candidates, context_candidates
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # unknown / malformed subject key
        raise HTTPException(
            status_code=400, detail=f"invalid or unresolved subject key: {exc}"
        )
