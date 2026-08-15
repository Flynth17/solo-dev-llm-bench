"""FastAPI backend for Solo Dev LLM Bench."""

import logging
from datetime import datetime, timezone
from pathlib import Path

import json
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.benchmark_markdown import run_markdown_benchmark, DEFAULT_PROMPTS as MD_PROMPTS
from src.benchmark_python import run_python_benchmark, DEFAULT_PROMPTS as PY_PROMPTS
from src.benchmark_java import run_java_benchmark, DEFAULT_PROMPTS as JA_PROMPTS
from src.task_markdown import run_markdown_task, TASK_DEFINITION as MD_TASK_DEF
from src.config_loader import load_config, save_config
from src import task_manager
from src import app_state
from src.routes import config as config_routes
from src.routes import models as models_routes
from src.routes import prompts as prompts_routes
from src.routes import results as results_routes
from src.routes import benchmark as benchmark_routes
from src.routes import tasks as tasks_routes

logger = logging.getLogger("solo_dev_llm_bench")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Solo Dev LLM Bench", version="1.0.0")

# Serve static files from the static/ directory
STATIC_DIR = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Shared results store singleton
results_store = app_state.results_store

# Register config routes
app.include_router(config_routes.router)

# Register models route
app.include_router(models_routes.router)

# Register prompts route
app.include_router(prompts_routes.router)

# Register results route
app.include_router(results_routes.router)

# Register benchmark route
app.include_router(benchmark_routes.router)

# Register task CRUD and history routes
app.include_router(tasks_routes.router)

# Initialize tasks table
task_manager.init_tasks_table()

# Task prompts registry
_TASK_PROMPTS = {
    "markdown": MD_PROMPTS,
    "python": PY_PROMPTS,
    "java": JA_PROMPTS,
}

# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard HTML page."""
    index_file = STATIC_DIR / "index.html"
    return index_file.read_text(encoding="utf-8")


@app.get("/results", response_class=HTMLResponse)
async def past_results():
    """Serve the Past Results HTML page."""
    results_file = STATIC_DIR / "results.html"
    return results_file.read_text(encoding="utf-8")


# Task run endpoint (remains in main.py)
# ---------------------------------------------------------------------------

@app.post("/api/tasks/{task_id}/run")
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
            result = await run_python_benchmark(
                lm_studio_url=lm_studio_url,
                model=model,
                prompt=task["prompt"] or PY_PROMPTS[0]["prompt"],
                max_tokens=max_tokens,
                temperature=temperature,
                iterations=iterations,
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




