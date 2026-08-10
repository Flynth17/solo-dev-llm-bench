"""FastAPI backend for Solo Dev LLM Bench."""

import logging
from pathlib import Path

import json
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.benchmark import fetch_models, run_benchmark
from src.config_loader import load_config, save_config
from src.results import ResultsStore

logger = logging.getLogger("solo_dev_llm_bench")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Solo Dev LLM Bench", version="1.0.0")

# Serve static files from the static/ directory
STATIC_DIR = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Global results store
results_store = ResultsStore()

# ---------------------------------------------------------------------------
# Prompts storage helpers
# ---------------------------------------------------------------------------

_PROMPTS_PATH = Path(__file__).parent.parent / "data" / "prompts.json"

_DEFAULT_PROMPTS = [
    {
        "name": "500 Token General Benchmark",
        "prompt": "Write a short story about a robot learning to feel emotions."
    }
]


def _load_prompts() -> list:
    """Load saved prompts from disk, or return defaults if file doesn't exist."""
    if _PROMPTS_PATH.exists():
        try:
            data = json.loads(_PROMPTS_PATH.read_text(encoding="utf-8"))
            return data.get("prompts", _DEFAULT_PROMPTS)
        except (json.JSONDecodeError, KeyError):
            return list(_DEFAULT_PROMPTS)
    return list(_DEFAULT_PROMPTS)


def _save_prompts(prompts: list) -> None:
    """Save prompts list to disk."""
    _PROMPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROMPTS_PATH.write_text(json.dumps({"prompts": prompts}, indent=2, ensure_ascii=False), encoding="utf-8")


# Initialize prompts cache
_prompts_cache = _load_prompts()

# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard HTML page."""
    index_file = STATIC_DIR / "index.html"
    return index_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------


@app.get("/api/config")
async def get_config():
    """Return current configuration."""
    return load_config()


@app.post("/api/config")
async def update_config(config: dict):
    """Save updated configuration."""
    save_config(config)
    return {"status": "ok", "config": config}


# ---------------------------------------------------------------------------
# Models endpoint
# ---------------------------------------------------------------------------


@app.get("/api/models")
async def get_models():
    """Fetch LLM models from LM Studio native v1 API."""
    config = load_config()
    lm_studio_url = config.get("lm_studio_url", "http://localhost:1234").rstrip("/")
    try:
        models = await fetch_models(lm_studio_url)
        return {"models": models}
    except httpx.HTTPStatusError as e:
        logger.error(
            "HTTP error from LM Studio: %s %s — status %s",
            e.request.method, e.request.url, e.response.status_code,
        )
        raise HTTPException(status_code=502, detail=f"LM Studio HTTP {e.response.status_code}: {e.response.text[:500]}")
    except httpx.RequestError as e:
        logger.error(
            "Connection error reaching LM Studio at %s: %s", lm_studio_url, e
        )
        raise HTTPException(status_code=502, detail=f"Cannot connect to LM Studio at {lm_studio_url}: {e}")
    except Exception as e:
        logger.error(
            "Unexpected error fetching models from LM Studio: %s — %s", type(e).__name__, e
        )
        raise HTTPException(status_code=502, detail=f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# Benchmark endpoints
# ---------------------------------------------------------------------------


@app.post("/api/benchmark/run")
async def run_benchmark_endpoint(config: dict):
    """Run a benchmark and append results."""
    model = config.get("model", "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Model must be specified")

    prompt = config.get("prompt", "")
    prompt_name = config.get("prompt_name", config.get("prompt_label", ""))

    # Validate iterations with safe bounds
    try:
        iterations = int(config.get("iterations", 5))
        if iterations < 1 or iterations > 100:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Iterations must be an integer between 1 and 100")

    # Validate max_tokens
    try:
        max_tokens = int(config.get("max_tokens", 500))
        if max_tokens < 1 or max_tokens > 10000:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="max_tokens must be an integer between 1 and 10000")

    # Validate temperature
    try:
        temperature = float(config.get("temperature", 0))
        if temperature < 0 or temperature > 2:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="temperature must be a number between 0 and 2")

    lm_studio_url = config.get("lm_studio_url", "http://localhost:1234")
    hardware_label = config.get("hardware_label", "")
    execution_environment = config.get("execution_environment", "Local")
    connection_type = config.get("connection_type", "")

    try:
        benchmark_result = await run_benchmark(
            lm_studio_url=lm_studio_url,
            model=model,
            prompt=prompt,
            iterations=iterations,
            max_tokens=max_tokens,
            temperature=temperature,
            hardware_label=hardware_label,
            execution_environment=execution_environment,
            connection_type=connection_type,
            prompt_name=prompt_name,
        )
    except Exception as e:
        logger.error("Benchmark failed for model %s: %s — %s", model, type(e).__name__, e)
        raise HTTPException(status_code=502, detail=f"Benchmark failed: {e}")

    # Persist each iteration as a separate CSV row
    run_id = benchmark_result["run_id"]
    timestamp = benchmark_result["timestamp"]
    model_key = benchmark_result["model"]
    model_display_name = benchmark_result.get("model", model_key)

    for run in benchmark_result["runs"]:
        row = {
            "timestamp": timestamp,
            "run_id": run_id,
            "model_key": model_key,
            "model_display_name": model_display_name,
            "hardware_label": hardware_label,
            "execution_environment": execution_environment,
            "connection_type": connection_type,
            "iteration": run["iteration"],
            "cold_or_warm": run["cold_or_warm"],
            "tokens_per_second": run["tokens_per_second"],
            "ttft_seconds": run["ttft_seconds"],
            "input_tokens": run["input_tokens"],
            "output_tokens": run["output_tokens"],
            "model_load_time_seconds": run.get("model_load_time_seconds"),
            "wall_time_seconds": run["wall_time_seconds"],
            "prompt_name": prompt_name,
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        results_store.add_run(row)

    return {"status": "ok", "result": benchmark_result}


# ---------------------------------------------------------------------------
# Prompts endpoints
# ---------------------------------------------------------------------------


@app.get("/api/prompts")
async def get_prompts():
    """Return all saved prompts."""
    global _prompts_cache
    _prompts_cache = _load_prompts()
    return {"prompts": _prompts_cache}


@app.post("/api/prompts")
async def create_prompt(body: dict):
    """Save a new prompt."""
    global _prompts_cache
    name = (body.get("name") or "").strip()
    prompt_text = body.get("prompt") or ""
    if not name:
        raise HTTPException(status_code=400, detail="Prompt name cannot be empty")
    _prompts_cache = _load_prompts()
    # Check for duplicates (case-insensitive)
    for p in _prompts_cache:
        if p["name"].lower() == name.lower():
            raise HTTPException(status_code=409, detail=f"A prompt with the name '{name}' already exists")
    _prompts_cache.append({"name": name, "prompt": prompt_text})
    _save_prompts(_prompts_cache)
    return {"status": "ok", "prompts": _prompts_cache}


@app.put("/api/prompts/{name}")
async def update_prompt(name: str, body: dict):
    """Rename an existing prompt."""
    global _prompts_cache
    old_name = name  # URL param is the old name
    new_name = (body.get("name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Prompt name cannot be empty")
    _prompts_cache = _load_prompts()
    found = False
    for i, p in enumerate(_prompts_cache):
        if p["name"] == old_name:
            # Check duplicate with new name
            for j, q in enumerate(_prompts_cache):
                if j != i and q["name"].lower() == new_name.lower():
                    raise HTTPException(status_code=409, detail=f"A prompt with the name '{new_name}' already exists")
            _prompts_cache[i]["name"] = new_name
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail=f"Prompt '{old_name}' not found")
    _save_prompts(_prompts_cache)
    return {"status": "ok", "prompts": _prompts_cache}


@app.delete("/api/prompts/{name}")
async def delete_prompt(name: str):
    """Delete a prompt by name."""
    global _prompts_cache
    _prompts_cache = _load_prompts()
    original_len = len(_prompts_cache)
    _prompts_cache = [p for p in _prompts_cache if p["name"] != name]
    if len(_prompts_cache) == original_len:
        raise HTTPException(status_code=404, detail=f"Prompt '{name}' not found")
    _save_prompts(_prompts_cache)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Legacy group-by-run endpoint for backward compatibility
# ---------------------------------------------------------------------------

@app.get("/api/benchmark/runs/grouped")
async def get_grouped_results():
    """Return results grouped by run_id (for dashboard compatibility)."""
    all_runs = results_store.get_all()

    # Group by run_id
    groups: dict[str, dict] = {}
    for run in all_runs:
        rid = run.get("run_id", "")
        if not rid:
            continue
        if rid not in groups:
            groups[rid] = {
                "run_id": rid,
                "timestamp": run.get("timestamp", ""),
                "model": run.get("model_key", ""),
                "model_display_name": run.get("model_display_name", ""),
                "hardware_label": run.get("hardware_label", ""),
                "execution_environment": run.get("execution_environment", ""),
                "connection_type": run.get("connection_type", ""),
                "prompt_name": run.get("prompt_name", ""),
                "iterations": 0,
                "runs": [],
                "aggregate": {"avg_tokens_per_second": 0, "min_tokens_per_second": 0, "max_tokens_per_second": 0},
                "warm_aggregate": {"avg_tokens_per_second": None, "avg_ttft": None, "available": False},
            }
        groups[rid]["runs"].append(run)
        groups[rid]["iterations"] += 1

    # Compute aggregates per group
    for rid, group in groups.items():
        tps_values = [r["tokens_per_second"] for r in group["runs"] if r.get("tokens_per_second", 0) > 0]
        if tps_values:
            group["aggregate"] = {
                "avg_tokens_per_second": round(sum(tps_values) / len(tps_values), 2),
                "min_tokens_per_second": round(min(tps_values), 2),
                "max_tokens_per_second": round(max(tps_values), 2),
            }

        warm_tps = [r["tokens_per_second"] for r in group["runs"] if r.get("cold_or_warm") == "warm" and r.get("tokens_per_second", 0) > 0]
        warm_ttfts = [r["ttft_seconds"] for r in group["runs"] if r.get("cold_or_warm") == "warm"]
        if warm_tps:
            group["warm_aggregate"] = {
                "avg_tokens_per_second": round(sum(warm_tps) / len(warm_tps), 2),
                "avg_ttft": round(sum(warm_ttfts) / len(warm_ttfts), 2) if warm_ttfts else None,
                "available": True,
            }

    return {"results": list(groups.values())}