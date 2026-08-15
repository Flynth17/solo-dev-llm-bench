"""Task CRUD and history routes for Solo Dev LLM Bench."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from src import task_manager
from src.benchmark_markdown import run_markdown_benchmark, DEFAULT_PROMPTS as MD_PROMPTS
from src.benchmark_java import run_java_benchmark, DEFAULT_PROMPTS as JA_PROMPTS
from src.task_markdown import run_markdown_task, TASK_DEFINITION as MD_TASK_DEF
from src.task_python import run_python_correctness_task, TASK_DEFINITION as PY_TASK_DEF

logger = logging.getLogger("solo_dev_llm_bench")

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


@router.post("/api/tasks/{task_id}/run")
async def run_task(task_id: str, config: dict):
    """Execute a benchmark task."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    if task["status"] == "running":
        raise HTTPException(status_code=409, detail="Task is already running")

    model = config.get("model", "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Model must be specified")

    lm_studio_url = config.get("lm_studio_url", "http://localhost:1234")
    max_tokens = int(config.get("max_tokens", 500))
    temperature = float(config.get("temperature", 0))
    iterations = int(config.get("iterations", 3))

    task_manager.update_task_status(task_id, "running")

    try:
        if task["task_type"] == "markdown":
            result = await run_markdown_task(
                lm_studio_url=lm_studio_url,
                model=model,
                fixture_name=MD_TASK_DEF["fixture_dir"] + "/broken.md",
                max_output_tokens=int(config.get("max_output_tokens", MD_TASK_DEF["max_output_tokens"])),
                temperature=float(config.get("temperature", MD_TASK_DEF["temperature"])),
                hardware_label=config.get("hardware_label", ""),
                execution_environment=config.get("execution_environment", "Local"),
                connection_type=config.get("connection_type", ""),
            )
        elif task["task_type"] == "python":
            result = await run_python_correctness_task(
                lm_studio_url=lm_studio_url,
                model=model,
                fixture_name="python_correctness/solution.py",
                max_output_tokens=max_tokens,
                temperature=float(config.get("temperature", PY_TASK_DEF["temperature"])),
                hardware_label=config.get("hardware_label", ""),
                execution_environment=config.get("execution_environment", "Local"),
                connection_type=config.get("connection_type", ""),
            )
        elif task["task_type"] == "java":
            result = await run_java_benchmark(
                lm_studio_url=lm_studio_url,
                model=model,
                prompt=task["prompt"] or JA_PROMPTS[0]["prompt"],
                max_tokens=max_tokens,
                temperature=temperature,
                iterations=iterations,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown task type: {task['task_type']}")

        # Save result to task table (last result)
        task_manager.set_task_result(task_id, result)

        # Persist as historical task run
        passed_val = result.get("passed", None)
        score_val = result.get("score", None)
        task_manager.create_task_run(
            task_id=task_id,
            task_name=result.get("task_name", task["name"]),
            task_type=result.get("task_type", task["task_type"]),
            model=model,
            timestamp=datetime.now(timezone.utc).isoformat(),
            passed=passed_val,
            score=score_val,
            initial_errors=result.get("initial_errors"),
            final_errors=result.get("final_errors"),
            errors_fixed=result.get("errors_fixed"),
            output_tokens=result.get("output_tokens"),
            input_tokens=result.get("input_tokens"),
            tokens_per_second=result.get("tokens_per_second"),
            ttft_seconds=result.get("ttft_seconds"),
            wall_time_seconds=result.get("wall_time_seconds"),
            result=result,
        )

        # Also persist to results store
        import src.app_state
        results_store = src.app_state.results_store
        run_id = f"task-{task_id}-run"
        timestamp = datetime.now(timezone.utc).isoformat()
        for run in result.get("runs", []):
            row = {
                "timestamp": timestamp,
                "run_id": run_id,
                "model_key": model,
                "model_display_name": model,
                "hardware_label": config.get("hardware_label", ""),
                "execution_environment": config.get("execution_environment", "Local"),
                "connection_type": config.get("connection_type", ""),
                "iteration": run["iteration"],
                "cold_or_warm": run["cold_or_warm"],
                "tokens_per_second": run["tokens_per_second"],
                "ttft_seconds": run["ttft_seconds"],
                "input_tokens": run["input_tokens"],
                "output_tokens": run["output_tokens"],
                "model_load_time_seconds": run.get("model_load_time_seconds"),
                "wall_time_seconds": run["wall_time_seconds"],
                "prompt_name": task["name"],
                "max_output_tokens": max_tokens,
                "temperature": temperature,
                # Task-specific fields
                f"{task['task_type']}_score": run.get(f"{task['task_type']}_score"),
                f"{task['task_type']}_meets_minimum": run.get(f"{task['task_type']}_meets_minimum"),
            }
            results_store.add_run(row)

        task_manager.update_task_status(task_id, "completed", run_id=run_id)
        return {"status": "ok", "result": result}

    except Exception as e:
        task_manager.update_task_status(task_id, "failed")
        logger.error("Task %s failed: %s — %s", task_id, type(e).__name__, e)
        raise HTTPException(status_code=502, detail=f"Task failed: {e}")
