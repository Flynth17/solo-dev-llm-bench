"""Context benchmark family launcher API (RM-26-AA-0009).

Thin HTTP adapter over :mod:`src.v2_context_launcher` /
:mod:`src.v2_context_read_model` -- execution plumbing + read-only result access.
It performs NO benchmark logic and never imports or calls any executor, builder
or scorer.

    POST   /api/context/run                          -> 202 {context_run_id, status}
        (409 if another standard run is already active; 400 if no model selected)
    GET    /api/context/runs/{run_id}/status         -> 200 | 400 | 404
    GET    /api/context/runs/{run_id}                -> 200 read model | 400 | 404 | 500

Completion is proven by the durable artifact written by the locked runner; no
database-backed queue is used. The single-run read endpoint delegates entirely to
:func:`src.v2_context_read_model.load_context_result` -- it surfaces authoritative
per-point data verbatim and recomputes nothing.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from src.v2_context_launcher import launch_context, context_status, ContextLaunchError
from src.v2_context_read_model import (
    load_context_result,
    ContextReadModelIntegrityError,
    ContextRunNotFoundError,
)
from src.backend_url_policy import validate_backend_url, resolve_allowed_hosts, BackendUrlPolicyError

logger = logging.getLogger("solo_dev_llm_bench")

router = APIRouter()


@router.post("/api/context/run")
async def run_context_endpoint(config: dict):
    """Safely start the locked Context runner in a separate OS process.

    Only model + runtime identity are accepted. Any context-bin / point override is
    intentionally ignored -- the fixed contract belongs to the locked runner.
    """
    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    model = config.get("model")
    lm_studio_url = config.get("lm_studio_url")

    if isinstance(lm_studio_url, str):
        try:
            validate_backend_url(lm_studio_url, resolve_allowed_hosts())
        except BackendUrlPolicyError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid backend URL: {exc}") from exc

    try:
        result = launch_context(model, lm_studio_url)
    except ContextLaunchError as exc:
        detail = str(exc) or "Context launch failed"
        if "already in progress" in detail.lower():
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=400, detail=detail)

    return JSONResponse(content=result, status_code=202)


@router.get("/api/context/runs/{run_id}/status")
def context_status_endpoint(run_id: str):
    """Return the lifecycle status of a Context run (read-only)."""
    status_code, body = context_status(run_id)

    if status_code == 400:
        raise HTTPException(status_code=400, detail=body.get("error", "Invalid run_id"))
    if status_code == 404:
        raise HTTPException(status_code=404, detail=body.get("error", "Not found"))

    return body


@router.get("/api/context/runs/{run_id}")
def context_run_endpoint(run_id: str):
    """Return the normalized single-run Context result (read only).

    Delegates entirely to the validated read model; performs NO benchmark logic and
    never recomputes scores. Error mapping mirrors the other suites:

        400 unsafe / malformed run id (path-traversal guard)
        404 unknown run id
        500 integrity-broken persisted run OR store read failure
    """
    try:
        from src.v2_context_artifact import _validate_run_id

        _validate_run_id(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        return load_context_result(run_id)
    except ContextRunNotFoundError:
        raise HTTPException(status_code=404, detail=f"Context run '{run_id}' not found")
    except ContextReadModelIntegrityError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid persisted Context data: {exc}")
    except Exception:  # pragma: no cover - defensive; missing data never leaks internals
        raise HTTPException(status_code=500, detail="Unable to read persisted context data")
