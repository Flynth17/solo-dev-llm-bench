"""Benchmark routes for Solo Dev LLM Bench."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

import src.app_state
from src.benchmark import run_benchmark

logger = logging.getLogger("solo_dev_llm_bench")

router = APIRouter()


def _get_results_store():
    """Get the current results_store from app_state module."""
    return src.app_state.results_store


@router.post("/api/benchmark/run")
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

    results_store = _get_results_store()
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


@router.get("/api/benchmark/runs/grouped")
async def get_grouped_results():
    """Return results grouped by run_id (for dashboard compatibility)."""
    results_store = _get_results_store()
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