"""Standard Speed Suite launcher API (Act 17).

Thin HTTP adapter over :mod:`src.v2_speed_suite_runner` -- execution plumbing only. It
performs NO benchmark logic and never imports or calls the legacy executor.

    POST   /api/v2/speed/run                            -> 202 {speed_run_id, status}
        (409 if another standard run is already active; 400 if no model selected)
    GET    /api/v2/speed/runs/{speed_run_id}/status     -> 200 | 400 | 404

The fixed Standard Speed contract (8K / 16K / 32K, fixed iterations and output budget)
lives entirely in the runner. This route exposes only model + runtime identity and
deliberately IGNORES any attempt to override context bins, iteration count or output
length -- those belong to Advanced / Custom Benchmark.

Lifecycle states mirror Act 16: ``running`` -> ``completed`` (+ result_url) or
``failed`` (a concise, safe reason). Completion is proven by durable rows persisted to
the shared ResultsStore; no database-backed queue is used.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from src.v2_speed_suite_runner import launch_speed, speed_status, SpeedLaunchError

logger = logging.getLogger("solo_dev_llm_bench")

router = APIRouter()


@router.post("/api/v2/speed/run")
async def run_speed_endpoint(config: dict):
    """Safely start the standard Speed runner as a separate OS process.

    Only model + runtime identity are accepted. Any ``context_points``, ``iterations``
    or ``max_output_tokens`` supplied by the client are intentionally ignored so the
    fixed contract cannot be overridden from this route.
    """
    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    model = config.get("model")
    lm_studio_url = config.get("lm_studio_url")
    # hardware_label is optional metadata; never treated as an override knob.
    hardware_label = config.get("hardware_label", "") if isinstance(config.get("hardware_label"), str) else ""

    try:
        result = launch_speed(model, lm_studio_url, hardware_label=hardware_label)
    except SpeedLaunchError as exc:
        detail = str(exc) or "Speed launch failed"
        # Concurrency guard -> 409; anything else (e.g. missing model) -> 400.
        if "already in progress" in detail.lower():
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=400, detail=detail)

    # The process is launched asynchronously; the request returns immediately with a
    # 202 Accepted so the FastAPI worker is never held hostage by a long benchmark.
    return JSONResponse(content=result, status_code=202)


@router.get("/api/v2/speed/runs/{speed_run_id}/status")
def speed_status_endpoint(speed_run_id: str):
    """Return the lifecycle status of a standard Speed run (read-only)."""
    status_code, body = speed_status(speed_run_id)

    if status_code == 400:
        raise HTTPException(status_code=400, detail=body.get("error", "Invalid run_id"))
    if status_code == 404:
        raise HTTPException(status_code=404, detail=body.get("error", "Not found"))

    return body
