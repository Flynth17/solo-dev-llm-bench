"""Task CRUD and history routes for Solo Dev LLM Bench."""

from fastapi import APIRouter, HTTPException

from src import task_manager

router = APIRouter()


@router.get("/api/tasks")
async def get_tasks():
    """Return all benchmark tasks, newest first."""
    tasks = task_manager.get_tasks()
    return {"tasks": tasks}


@router.post("/api/tasks")
async def create_task(body: dict):
    """Create a new benchmark task."""
    name = (body.get("name") or "").strip()
    task_type = (body.get("task_type") or "").strip()
    prompt = body.get("prompt", "")

    if not name or not task_type:
        raise HTTPException(status_code=400, detail="name and task_type are required")
    if task_type not in ("markdown", "python", "java"):
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
    """
    runs = task_manager.get_task_runs(task_type=task_type)
    return {"tasks": runs}


@router.delete("/api/task-runs/{run_id}")
async def delete_task_run(run_id: int):
    """Delete a single historical task run."""
    deleted = task_manager.delete_task_run(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task run {run_id} not found")
    return {"status": "ok", "run_id": run_id}