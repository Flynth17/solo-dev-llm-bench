"""Models route for Solo Dev LLM Bench."""

import httpx
import logging
from fastapi import APIRouter, HTTPException

from src.config_loader import load_config
from src.benchmark import fetch_models
from src.model_lifecycle import (
    load_model as lifecycle_load,
    unload_model as lifecycle_unload,
    ModelLifecycleError,
)  # noqa: E402
import src.standard_run_guard as standard_run_guard  # noqa: E402

logger = logging.getLogger("solo_dev_llm_bench")

router = APIRouter()

# Standard-suite lifecycle mutations (Act 18) must never coincide with an active
# benchmark -- loading or unloading a model mid-run would corrupt measurements. The
# shared guard is authoritative; frontend gating is only a convenience.
def _reject_if_benchmark_active():
    """Raise HTTP 409 while any standard Speed/Workflow run is in flight (or None)."""
    active = standard_run_guard.is_standard_run_active()
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"A standard benchmark is already in progress ({active}). "
                   "Wait for it to finish before loading or unloading a model.",
        )


def _resolve_url(request_body: dict) -> str:
    """Prefer the caller-provided LM Studio URL, else the configured default."""
    provided = request_body.get("lm_studio_url")
    if isinstance(provided, str) and provided.strip():
        return provided.strip().rstrip("/")
    return (load_config().get("lm_studio_url") or "http://localhost:1234").rstrip("/")


def _map_lifecycle(exc: ModelLifecycleError) -> HTTPException:
    """Translate a lifecycle failure into a concise, safe HTTP response."""
    detail = exc.message or "Model operation failed"
    return HTTPException(status_code=exc.status_code, detail=f"{exc.reason}: {detail}")


@router.get("/api/models")
async def get_models():
    """Fetch LLM models from LM Studio native v1 API."""
    config = load_config()
    lm_studio_url = config.get("lm_studio_url", "http://localhost:1234").rstrip("/")
    try:
        models = await fetch_models(lm_studio_url)
        return {"models": models}
    except httpx.HTTPStatusError as e:
        logger.error(
            "HTTP error from LM Studio: %s %s — status %s",
            e.request.method, e.request.url, e.response.status_code,
        )
        raise HTTPException(status_code=502, detail=f"LM Studio HTTP {e.response.status_code}: {e.response.text[:500]}")
    except httpx.RequestError as e:
        logger.error(
            "Connection error reaching LM Studio at %s: %s", lm_studio_url, e
        )
        raise HTTPException(status_code=502, detail=f"Cannot connect to LM Studio at {lm_studio_url}: {e}")
    except Exception as e:
        logger.error(
            "Unexpected error fetching models from LM Studio: %s — %s", type(e).__name__, e
        )
        raise HTTPException(status_code=502, detail=f"Unexpected error: {e}")


@router.post("/api/models/load")
async def load_model_endpoint(request_body: dict):
    """Load the selected model at the standard context target (Act 18).

    Minimal request::

        { "model": "ornith-1.5-35b-a3b", "lm_studio_url": "http://localhost:1234" }

    Only the standard context target is applied; arbitrary context / batch / flash-
    attention / MTP / expert / KV-cache / GPU-offload overrides are rejected at the
    adapter layer. Reuses GET /api/models for refresh/state.
    """
    if not isinstance(request_body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    model = request_body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise HTTPException(status_code=400, detail="A model must be selected to load.")

    _reject_if_benchmark_active()
    base_url = _resolve_url(request_body)

    try:
        result = await lifecycle_load(base_url, model.strip())
    except ModelLifecycleError as exc:
        raise _map_lifecycle(exc)
    except httpx.HTTPStatusError as e:
        logger.error("HTTP error from LM Studio load: %s -- status %s", e.request.url, e.response.status_code)
        raise HTTPException(status_code=502, detail=f"LM Studio HTTP {e.response.status_code}: {e.response.text[:300]}")
    except httpx.RequestError as e:
        logger.error("Connection error reaching LM Studio at %s: %s", base_url, e)
        raise HTTPException(status_code=502, detail=f"Cannot connect to LM Studio at {base_url}: {e}")

    return result


@router.post("/api/models/unload")
async def unload_model_endpoint(request_body: dict):
    """Unload the selected model's actual instance id (Act 18).

    Operates on the real LM Studio ``instance_id`` (never assumes key == instance id).
    Zero instances -> explicit already-unloaded response; multiple instances -> ambiguity
    error. Reuses GET /api/models for refresh/state.
    """
    if not isinstance(request_body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    model = request_body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise HTTPException(status_code=400, detail="A model must be selected to unload.")

    _reject_if_benchmark_active()
    base_url = _resolve_url(request_body)

    try:
        result = await lifecycle_unload(base_url, model.strip())
    except ModelLifecycleError as exc:
        raise _map_lifecycle(exc)
    except httpx.HTTPStatusError as e:
        logger.error("HTTP error from LM Studio unload: %s -- status %s", e.request.url, e.response.status_code)
        raise HTTPException(status_code=502, detail=f"LM Studio HTTP {e.response.status_code}: {e.response.text[:300]}")
    except httpx.RequestError as e:
        logger.error("Connection error reaching LM Studio at %s: %s", base_url, e)
        raise HTTPException(status_code=502, detail=f"Cannot connect to LM Studio at {base_url}: {e}")

    return result