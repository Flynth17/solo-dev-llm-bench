"""Task CRUD and history routes for Solo Dev LLM Bench."""

from fastapi import APIRouter, HTTPException

from src import task_manager

router = APIRouter()


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete a benchmark task."""
    deleted = task_manager.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return {"status": "ok", "task_id": task_id}


@router.get("/api/tasks-with-results")
async def get_tasks_with_results(task_type: str | None = None):
    """Return persistent historical task runs for Task History.

    Supports optional ?task_type=markdown|python|java|unsolvable filter.

    Java normalization: both "java" and the legacy "java_correctness" values
    are treated as the same Java Correctness category so that existing
    historical rows (stored as java_correctness) appear when the UI filters
    by task_type=java.
    """
    if task_type == "java":
        # Match both current and legacy Java task_type values
        runs = task_manager.get_task_runs(task_type="java") + task_manager.get_task_runs(task_type="java_correctness")
        return {"tasks": runs}
    runs = task_manager.get_task_runs(task_type=task_type)
    return {"tasks": runs}


@router.delete("/api/task-runs/{run_id}")
async def delete_task_run(run_id: int):
    """Delete a single historical task run."""
    deleted = task_manager.delete_task_run(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task run {run_id} not found")
    return {"status": "ok", "run_id": run_id}
