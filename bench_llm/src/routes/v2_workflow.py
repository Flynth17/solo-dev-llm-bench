"""Workflow suite launcher API (Act 16).

Thin HTTP adapter over :mod:`src.v2_workflow_runner` -- execution plumbing only. It
performs NO benchmark logic and never imports or calls the legacy executor.

    POST   /api/v2/workflow/run                       -> 202 {run_id, status}
        (409 if a Workflow run is already active; 400 if no model selected)
    GET    /api/v2/workflow/runs/{run_id}/status      -> 200 | 400 | 404

Lifecycle states: ``running`` -> ``completed`` (+ ``result_url``) or ``failed``
(a concise, safe reason). No database-backed queue: completion is proven by the
durable artifact written by the locked suite runner.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from src.v2_workflow_runner import launch_workflow, workflow_status, WorkflowLaunchError

logger = logging.getLogger("solo_dev_llm_bench")

router = APIRouter()


@router.post("/api/v2/workflow/run")
async def run_workflow_endpoint(config: dict):
    """Safely start the locked workflow runner in a separate OS process.

    Validates only what is needed to identify the selected model and runtime; it does
    NOT expose individual-suite, temperature, output-token, reasoning-effort or
    output-policy overrides -- those are decided by the locked Workflow contract.
    """
    model = config.get("model") if isinstance(config, dict) else None
    lm_studio_url = config.get("lm_studio_url") if isinstance(config, dict) else None

    try:
        result = launch_workflow(model, lm_studio_url)
    except WorkflowLaunchError as exc:
        detail = str(exc) or "Workflow launch failed"
        # Concurrency guard -> 409; anything else (e.g. missing model) -> 400.
        if "already in progress" in detail.lower():
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=400, detail=detail)

    # The process is launched asynchronously; the request returns immediately with a
    # 202 Accepted so the FastAPI worker is never held hostage by a long benchmark.
    return JSONResponse(content=result, status_code=202)


@router.get("/api/v2/workflow/runs/{run_id}/status")
def workflow_status_endpoint(run_id: str):
    """Return the lifecycle status of a Workflow run (read-only)."""
    status_code, body = workflow_status(run_id)

    if status_code == 400:
        raise HTTPException(status_code=400, detail=body.get("error", "Invalid run_id"))
    if status_code == 404:
        raise HTTPException(status_code=404, detail=body.get("error", "Not found"))

    return body
