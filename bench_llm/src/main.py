"""FastAPI backend for Solo Dev LLM Bench."""

import logging
from datetime import datetime, timezone
from pathlib import Path

import json
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.config_loader import load_config, save_config
from src import task_manager
from src import app_state
from src.routes import config as config_routes
from src.routes import models as models_routes
from src.routes import prompts as prompts_routes
from src.routes import results as results_routes
from src.routes import benchmark as benchmark_routes
from src.routes import tasks as tasks_routes
from src.routes import evaluation as evaluation_routes
from src.routes import v2_results as v2_results_routes
from src.routes import v2_workflow as v2_workflow_routes
from src.routes import v2_speed as v2_speed_routes

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

# Register evaluation route
app.include_router(evaluation_routes.router)

# Register read-only V2 results API (consumes the validated read model only)
app.include_router(v2_results_routes.router)

# Register Workflow suite launcher API (Act 16: async process launch + status polling)
app.include_router(v2_workflow_routes.router)

# Register Standard Speed Suite launcher API (Act 17: async process launch + status polling).
# Shares the cross-suite concurrency guard with Workflow so only one standard benchmark
# uses the local model at a time.
app.include_router(v2_speed_routes.router)

# Initialize tasks table
task_manager.init_tasks_table()

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


@app.get("/v2/results/{run_id}", response_class=HTMLResponse)
async def v2_result_page(run_id: str):
    """Serve the V2 result summary page for any run ID.

    The static page derives ``run_id`` from the URL and fetches it via the read-only
    API at ``/api/v2/results/{run_id}`` (see src/routes/v2_results.py). No HTML file
    is generated per run; all data comes from the validated read model.
    """
    html_file = STATIC_DIR / "v2-result.html"
    return html_file.read_text(encoding="utf-8")


