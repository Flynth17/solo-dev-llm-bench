"""Read-only V2 results API (UI Act 4).

Thin HTTP adapter over the validated read model (:mod:`src.v2_quality_read_model`).
The route receives a ``run_id``, delegates to :func:`load_v2_result`, and returns
that JSON-compatible shape essentially verbatim. It performs NO benchmark logic:

* it never executes a benchmark;
* it never reads raw suite JSON or recomputes scores/denominators/``/166``;
* it never touches the legacy executor (``src.v2_quality_executor::run_v2_quality_live``)
  nor any SQLite store.

Error handling maps the read model's dedicated exceptions onto clean HTTP status
codes without leaking stack traces and without repairing bad data:

* :class:`V2RunNotFoundError`      -> ``404 Not Found``   (located? no)
* :class:`V2ArtifactSchemaError`   -> ``422 Unprocessable Entity`` (located, but
  this build cannot process the persisted format/version -- not a normal 404)
* :class:`V2ReadModelIntegrityError`-> ``500 Internal Server Error`` (persisted
  server-owned data is internally inconsistent; reported, never repaired)
* unsafe/malformed ``run_id`` (rejected by the loader's path guard) -> ``400``

Unexpected exceptions are deliberately NOT swallowed: they propagate and let
FastAPI return its default handling rather than hiding a bug behind a 500.
"""

from fastapi import APIRouter, HTTPException

# Error types come from their defining layers (persistence / read model).
from src.v2_quality_artifact import V2ArtifactSchemaError, V2RunNotFoundError

# Data path: the validated read model is the sole source of truth.
from src.v2_quality_read_model import V2ReadModelIntegrityError, load_v2_result
# Act 13: read-only dedicated-speed adapter (pure view model -- never executes a benchmark).
from src.v2_speed_read_model import load_speed_result

router = APIRouter()


@router.get("/api/v2/results/{run_id}")
async def get_v2_result(run_id: str):
    """Return the read model for one persisted V2 run (read only)."""
    try:
        # Authoritative source of truth: the validated read model. No re-derivation.
        return load_v2_result(run_id)
    except V2RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"V2 run '{run_id}' not found")
    except V2ArtifactSchemaError as exc:
        # The artifact exists but its persisted format/version is unprocessable here.
        raise HTTPException(status_code=422, detail=f"Unprocessable V2 artifact: {exc}")
    except V2ReadModelIntegrityError as exc:
        # Persisted server-owned data is inconsistent; report without repairing it.
        raise HTTPException(status_code=500, detail=f"Invalid V2 result data: {exc}")
    except ValueError:
        # Unsafe/malformed run_id rejected by the loader's path guard -- no duplicate
        # validation logic lives here; we only translate the rejection to HTTP.
        raise HTTPException(status_code=400, detail="Invalid run_id")


@router.get("/api/v2/results/{run_id}/speed")
async def get_v2_result_speed(run_id: str):
    """Return the *dedicated speed/performance benchmark* associated with a V2 run.

    Separate from quality telemetry: this exposes already-persisted dedicated speed
    rows (from the ResultsStore SQLite/CSV), matched to the quality result by explicit
    identity only -- never by assuming the two systems' fingerprints are equivalent, and
    never recomputed. It performs NO benchmark execution.

    Error handling mirrors the sibling quality route so a bad run id maps cleanly;
    failure here is independent of the main quality payload (the UI isolates it).
    """
    try:
        quality = load_v2_result(run_id)
    except V2RunNotFoundError:
        raise HTTPException(status_code=404, detail=f"V2 run '{run_id}' not found")
    except V2ArtifactSchemaError as exc:
        raise HTTPException(status_code=422, detail=f"Unprocessable V2 artifact: {exc}")
    except V2ReadModelIntegrityError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid V2 result data: {exc}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid run_id")

    # Minimal identity drawn from the quality read model -- first-class fields exposed by
    # BOTH systems (base model + loaded context window). Quantization is intentionally not
    # compared: the V2 result does not record it.
    quality_identity = {
        "model_identifier": quality.get("run", {}).get("model_identifier"),
        "loaded_context": quality.get("configuration", {}).get("loaded_context"),
    }

    try:
        import src.app_state  # imported locally to avoid any import-order coupling
        runs = src.app_state.results_store.get_all()  # read-only; no benchmark executed
        return load_speed_result(quality_identity, runs)
    except Exception as exc:  # pragma: no cover - defensive surface of store errors
        raise HTTPException(
            status_code=500,
            detail=f"Unable to read persisted speed data: {exc}",
        )
