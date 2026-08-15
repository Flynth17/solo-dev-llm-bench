"""Evaluation routes for Solo Dev LLM Bench.

Handles the new Run Evaluation flow for speed-only tests.
"""

import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException

import src.app_state
from src.evaluation_prompts import get_speed_prompt, PROMPT_FILES

logger = __import__("logging").getLogger("solo_dev_llm_bench")
router = APIRouter()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_results_store():
    """Get the current results_store from app_state module."""
    return src.app_state.results_store


def _validate_speed_tests(speed_tests: list) -> list[str]:
    """Validate and normalize the speed_tests list.

    - Reject empty lists
    - Reject unknown names
    - Deduplicate deterministically (preserve first occurrence order)
    """
    if not speed_tests:
        raise HTTPException(
            status_code=400,
            detail="speed_tests must not be empty",
        )

    # Check for unknown names
    for name in speed_tests:
        if name not in PROMPT_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown speed test: {name!r}",
            )

    # Deduplicate preserving first occurrence order
    seen: set[str] = set()
    deduped: list[str] = []
    for name in speed_tests:
        if name not in seen:
            seen.add(name)
            deduped.append(name)

    return deduped


def _aggregate_for_runs(runs: list[dict]) -> dict:
    """Compute aggregate stats for a list of benchmark runs."""
    tps_values = [r["tokens_per_second"] for r in runs if r.get("tokens_per_second", 0) > 0]
    ttft_values = [r["ttft_seconds"] for r in runs if r.get("ttft_seconds") is not None]
    wall_values = [r["wall_time_seconds"] for r in runs if r.get("wall_time_seconds") is not None]

    if tps_values:
        avg_tps = round(sum(tps_values) / len(tps_values), 2)
        min_tps = round(min(tps_values), 2)
        max_tps = round(max(tps_values), 2)
    else:
        avg_tps = 0
        min_tps = 0
        max_tps = 0

    return {
        "avg_tokens_per_second": avg_tps,
        "min_tokens_per_second": min_tps,
        "max_tokens_per_second": max_tps,
        "avg_ttft_seconds": round(sum(ttft_values) / len(ttft_values), 4) if ttft_values else None,
        "total_wall_time_seconds": round(sum(wall_values), 2) if wall_values else 0,
    }


def _warm_aggregate_for_runs(runs: list[dict]) -> dict:
    """Compute warm-only aggregate stats for a list of benchmark runs."""
    warm_runs = [r for r in runs if r.get("cold_or_warm") == "warm"]
    tps_values = [r["tokens_per_second"] for r in warm_runs if r.get("tokens_per_second", 0) > 0]
    ttft_values = [r["ttft_seconds"] for r in warm_runs if r.get("ttft_seconds") is not None]

    if tps_values:
        return {
            "avg_tokens_per_second": round(sum(tps_values) / len(tps_values), 2),
            "avg_ttft_seconds": round(sum(ttft_values) / len(ttft_values), 4) if ttft_values else None,
            "available": True,
        }
    return {"avg_tokens_per_second": None, "avg_ttft_seconds": None, "available": False}


# ------------------------------------------------------------------
# Prompt labels for UI
# ------------------------------------------------------------------

_PROMPT_LABELS = {
    "small": "Small Prompt",
    "medium": "Medium Prompt",
    "large": "Large Prompt",
}


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/api/evaluation/run")
async def run_evaluation_endpoint(config: dict):
    """Run selected speed tests and return results.

    Request body:
    {
        "lm_studio_url": "...",
        "model": "...",
        "execution_environment": "...",
        "connection_type": "...",
        "hardware_label": "...",
        "iterations": 5,
        "max_output_tokens": 1024,
        "temperature": 0,
        "speed_tests": ["small", "medium", "large"]
    }
    """
    # --- Validation ---
    speed_tests_raw = config.get("speed_tests", [])
    if not speed_tests_raw:
        raise HTTPException(
            status_code=400,
            detail="speed_tests must not be empty",
        )

    speed_tests = _validate_speed_tests(speed_tests_raw)

    model = config.get("model", "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Model must be specified")

    lm_studio_url = config.get("lm_studio_url", "http://localhost:1234")

    # Validate iterations
    try:
        iterations = int(config.get("iterations", 5))
        if iterations < 1 or iterations > 100:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="Iterations must be an integer between 1 and 100",
        )

    # Validate max_tokens
    try:
        max_tokens = int(config.get("max_output_tokens", config.get("max_tokens", 500)))
        if max_tokens < 1 or max_tokens > 10000:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="max_output_tokens must be an integer between 1 and 10000",
        )

    # Validate temperature
    try:
        temperature = float(config.get("temperature", 0))
        if temperature < 0 or temperature > 2:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="temperature must be a number between 0 and 2",
        )

    hardware_label = config.get("hardware_label", "")
    execution_environment = config.get("execution_environment", "Local")
    connection_type = config.get("connection_type", "")

    # --- Run selected speed tests sequentially ---
    results_store = _get_results_store()
    speed_results: list[dict] = []
    overall_tps_values: list[float] = []
    overall_ttft_values: list[float] = []
    total_wall_start = time.time()

    for test_name in speed_tests:
        test_wall_start = time.time()

        # Load canonical prompt from fixture
        try:
            prompt_text = get_speed_prompt(test_name)
        except ValueError as e:
            raise HTTPException(status_code=500, detail=str(e))

        prompt_label = _PROMPT_LABELS.get(test_name, test_name.title() + " Prompt")

        # Run benchmark engine
        from src.benchmark import run_benchmark

        try:
            benchmark_result = await run_benchmark(
                lm_studio_url=lm_studio_url,
                model=model,
                prompt=prompt_text,
                iterations=iterations,
                max_tokens=max_tokens,
                temperature=temperature,
                hardware_label=hardware_label,
                execution_environment=execution_environment,
                connection_type=connection_type,
                prompt_name=prompt_label,
            )
        except Exception as e:
            logger.error("Benchmark failed for speed test %s: %s — %s", test_name, type(e).__name__, e)
            raise HTTPException(
                status_code=502,
                detail=f"Benchmark failed for speed test {test_name!r}: {e}",
            )

        # Persist each iteration
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
                "prompt_name": prompt_label,
                "max_output_tokens": max_tokens,
                "temperature": temperature,
                "evaluation_test": test_name,
            }
            results_store.add_run(row)

        # Collect result for this speed test
        runs = benchmark_result["runs"]
        aggregate = _aggregate_for_runs(runs)
        warm_agg = _warm_aggregate_for_runs(runs)

        # Track for overall summary
        if aggregate["avg_tokens_per_second"] > 0:
            overall_tps_values.append(aggregate["avg_tokens_per_second"])
        if aggregate["avg_ttft_seconds"] is not None:
            overall_ttft_values.append(aggregate["avg_ttft_seconds"])

        speed_results.append({
            "test_name": test_name,
            "prompt_label": prompt_label,
            "runs": runs,
            "aggregate": aggregate,
            "warm_aggregate": warm_agg,
        })

    total_wall_time = round(time.time() - total_wall_start, 2)

    # Compute overall summary
    if overall_tps_values:
        avg_tps = round(sum(overall_tps_values) / len(overall_tps_values), 2)
    else:
        avg_tps = 0

    if overall_ttft_values:
        avg_ttft = round(sum(overall_ttft_values) / len(overall_ttft_values), 4)
    else:
        avg_ttft = 0

    return {
        "status": "completed",
        "model": model,
        "speed_results": speed_results,
        "summary": {
            "speed_tests_run": len(speed_results),
            "avg_tokens_per_second": avg_tps,
            "avg_ttft_seconds": avg_ttft,
            "total_wall_time_seconds": total_wall_time,
        },
    }