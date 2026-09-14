"""Task CRUD and history routes for Solo Dev LLM Bench."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from src import task_manager

# Canonical internal correctness-task labels. Filtered out of the user-facing task list
# so repeated Java/Python/Markdown/Unsolvable correctness rows do not flood it.
# These tasks are produced by the deterministic Workflow validators, not by any editable
# task runner (the legacy task runners were retired in Act 24).
_CANONICAL_EVALUATION_TASKS = frozenset({
    ("Markdownlint Default", "markdown"),
    ("Python Correctness", "python"),
    ("Java Correctness", "java"),
    ("Unsolvable Recognition", "unsolvable"),
})

logger = logging.getLogger("solo_dev_llm_bench")

router = APIRouter()


@router.get("/api/tasks")
async def get_tasks():
    """Return user-created benchmark tasks for Advanced Task Manager.

    Filters out internal canonical Evaluation Suite definitions so that
    repeated Java/Python/Markdown correctness rows do not flood the list.
    """
    all_tasks = task_manager.get_tasks()
    filtered = [
        t for t in all_tasks
        if (t.get("name"), t.get("task_type")) not in _CANONICAL_EVALUATION_TASKS
    ]
    return {"tasks": filtered}


@router.post("/api/tasks")
async def create_task(body: dict):
    """Create a new benchmark task."""
    name = (body.get("name") or "").strip()
    task_type = (body.get("task_type") or "").strip()
    prompt = body.get("prompt", "")

    if not name or not task_type:
        raise HTTPException(status_code=400, detail="name and task_type are required")
    if task_type not in ("markdown", "python", "java", "unsolvable"):
        raise HTTPException(status_code=400, detail=f"Invalid task_type: {task_type}")

    task = task_manager.create_task(name=name, task_type=task_type, prompt=prompt)
    return {"status": "ok", "task": task}


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

