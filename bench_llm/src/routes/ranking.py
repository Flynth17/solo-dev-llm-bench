"""Canonical ranking / aggregation API (Results UI prerequisite, read-only).

Thin HTTP adapter over :mod:`src.ranking_read_model`. The route exposes the
presentation-facing L0/L1/L2 ranking view built by
:func:`build_ranking` from already-persisted evidence only:

* **Speed** rows come straight from the shared ``ResultsStore`` (read-only, no
  benchmark executed).
* **Agentic / Workflow** views come from the validated V2 quality read model via
  :func:`load_v2_result`, one per persisted artifact.

It performs NO benchmark logic and never recomputes scores:

* it never executes a benchmark;
* it never reads raw suite JSON or recomputes denominators / ``/166``;
* it never touches the legacy executor nor any SQLite store write path.

Error handling mirrors the sibling V2 route so integrity failures map to clean
status codes without leaking internals or repairing bad data:

* unsafe / malformed ``run_id`` (rejected by the loader's path guard) -> ``400``
* :class:`V2ArtifactSchemaError`   -> ``422 Unprocessable Entity`` (located, but
  this build cannot process the persisted format/version)
* :class:`V2ReadModelIntegrityError`-> ``500 Internal Server Error`` (persisted
  server-owned data is internally inconsistent; reported, never repaired)

One invalid Workflow artifact must NOT collapse the whole ranking surface: a run
that fails to load is skipped from the Agentic dimension but still ranks on Speed
from its own persisted rows. Only an unrecoverable store read failure aborts the
whole response with ``500`` (the whole ranking could not be built).

No DB schema change is made or required -- this layer only reads existing rows /
artifacts and never writes.
"""

from __future__ import annotations

import glob
import logging
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

# Reuse verified readers from their defining layers (composition over replacement).
from src.ranking_read_model import build_ranking  # noqa: F401
from src.v2_quality_artifact import V2ArtifactSchemaError, V2RunNotFoundError
from src.v2_quality_read_model import (
    V2ReadModelIntegrityError,
    load_v2_result,  # noqa: F401 - re-exported for the agentic-view loader below
)

logger = logging.getLogger("solo_dev_llm_bench")

router = APIRouter()


def _v2_runs_dir() -> Path:
    """Return the directory holding persisted V2 workflow artifacts (read-only)."""
    # Reuse the canonical artifact dir declared by the persistence layer; never
    # invent a new path. Single source of truth, resolved at call time so callers/tests
    # can observe an isolated location without importing a stale binding.
    import src.v2_quality_artifact as art
    return art.V2_RUNS_DIR


def _load_agentic_views() -> list[dict[str, Any]]:
    """Load a validated workflow view per persisted artifact (read-only).

    Invalid artifacts are skipped individually so one bad run never collapses the
    whole Agentic dimension -- it simply does not contribute to ranking. This is
    deliberate degradation, not data repair.
    """
    views: list[dict[str, Any]] = []
    runs_dir = _v2_runs_dir()
    if not runs_dir.is_dir():
        return views

    # Only ``*.json`` artifacts are loadable documents; ``*.log`` files and other
    # extensions are ignored (never attempted as artifacts).
    for path in sorted(glob.glob(os.path.join(str(runs_dir), "*.json"))):
        run_id = Path(path).stem
        try:
            views.append(load_v2_result(run_id))
        except V2RunNotFoundError:
            # Artifact vanished between enumeration and load; skip (already gone).
            continue
        except (V2ArtifactSchemaError, V2ReadModelIntegrityError):
            # Located but unprocessable / internally inconsistent -- excluded from the
            # Agentic dimension without aborting the whole ranking surface.
            logger.warning("ranking: skipping invalid workflow artifact '%s'", run_id)
            continue
        except ValueError:
            # Unsafe/malformed run id rejected by the loader's path guard.
            continue
    return views


def _resolve_speed_runs() -> list[dict[str, Any]]:
    """Return all persisted Speed rows from the shared store (read-only)."""
    import src.app_state  # imported locally to avoid any import-order coupling
    return src.app_state.results_store.get_all()


@router.get("/api/ranking")
def ranking_endpoint() -> dict[str, Any]:
    """Return the canonical L0/L1/L2 ranking / aggregation view (read only).

    Combines Speed rows from the shared store with validated Workflow views and
    projects them into the presentation-facing representation the future UI
    consumes. Performs NO benchmark logic and invents no scores: composite scoring
    stays explicitly ``unavailable`` until its contract is approved.

    Error mapping:
        500 unrecoverable Speed store read failure (whole ranking could not build)
    """
    try:
        speed_runs = _resolve_speed_runs()
    except Exception:  # pragma: no cover - defensive; missing store never leaks internals
        raise HTTPException(
            status_code=500, detail="Unable to read persisted Speed data"
        )

    quality_views = _load_agentic_views()

    try:
        return build_ranking(speed_runs, quality_views)
    except Exception as exc:  # pragma: no cover - defensive surface of store errors
        raise HTTPException(
            status_code=500, detail=f"Unable to build ranking view: {exc}"
        )


@router.get("/api/ranking/summary")
def ranking_summary_endpoint() -> dict[str, Any]:
    """Return the compact summary used by a dashboard (L0 identity + dimensions).

    Only model identities and dimension availability are exposed -- not every
    configuration or run. Cheaper than :endpoint:`/api/ranking` for polling a grid.
    Read-only; no DB schema change, no benchmark execution.
    """
    try:
        speed_runs = _resolve_speed_runs()
    except Exception:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=500, detail="Unable to read persisted Speed data"
        )

    quality_views = _load_agentic_views()

    try:
        ranking = build_ranking(speed_runs, quality_views)
    except Exception as exc:  # pragma: no cover - defensive surface of store errors
        raise HTTPException(
            status_code=500, detail=f"Unable to build ranking view: {exc}"
        )

    return {
        "composite": ranking["composite"],
        "available_dimensions": ranking["available_dimensions"],
        "all_dimensions": ranking["all_dimensions"],
        "models": [
            {
                "model_version": m["model_version"],
                "model_family": m["model_family"],
                "architecture": m["architecture"],
                "rank": m["rank"],
                "has_approved_evidence": m["has_approved_evidence"],
                "dimensions": m["dimensions"],
            }
            for m in ranking["models"]
        ],
    }


@router.get("/api/ranking/models")
def ranking_models_endpoint() -> dict[str, Any]:
    """Return the distinct model identities available for ranking (L0 identity).

    One entry per ``(model_version, architecture)`` bucket so a UI can present the
    family/version list and distinguish Dense vs MoE. Read-only; no DB schema change.
    """
    try:
        speed_runs = _resolve_speed_runs()
    except Exception:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=500, detail="Unable to read persisted Speed data"
        )

    quality_views = _load_agentic_views()

    try:
        ranking = build_ranking(speed_runs, quality_views)
    except Exception as exc:  # pragma: no cover - defensive surface of store errors
        raise HTTPException(
            status_code=500, detail=f"Unable to build ranking view: {exc}"
        )

    return {
        "models": [
            {
                "model_version": m["model_version"],
                "model_family": m["model_family"],
                "architecture": m["architecture"],
                "rank": m["rank"],
                "has_approved_evidence": m["has_approved_evidence"],
            }
            for m in ranking["models"]
        ]
    }
