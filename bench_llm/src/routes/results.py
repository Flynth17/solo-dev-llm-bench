"""Results routes for Solo Dev LLM Bench."""

from fastapi import APIRouter, HTTPException

import src.app_state

router = APIRouter()


def _get_results_store():
    """Get the current results_store from app_state module."""
    return src.app_state.results_store


@router.get("/api/results")
async def get_past_results():
    """Return all benchmark results as individual rows, newest first."""
    results_store = _get_results_store()
    all_runs = results_store.get_all()

    # Sort by timestamp descending (newest first)
    all_runs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

    return {"results": all_runs}


@router.delete("/api/results/{run_id}")
async def delete_past_result(run_id: str):
    """Delete a single benchmark run by its run_id."""
    # Validate: reject arbitrary SQL or database identifiers
    if not run_id or "\x00" in run_id or "/" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run_id")

    results_store = _get_results_store()
    deleted = results_store.delete_run(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return {"status": "ok", "run_id": run_id}
