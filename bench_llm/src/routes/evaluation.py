"""Evaluation routes for Solo Dev LLM Bench.

Handles the Run Evaluation flow for speed + correctness tests.
"""

import asyncio
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException

import src.app_state
from src.evaluation_prompts import get_speed_prompt, PROMPT_FILES
import src.task_manager

logger = __import__("logging").getLogger("solo_dev_llm_bench")
router = APIRouter()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_results_store():
    """Get the current results_store from app_state module."""
    return src.app_state.results_store


# Valid correctness test names
CORRECTNESS_TESTS = {"markdown", "python", "java", "unsolvable"}

# Canonical task definitions
CANONICAL_TASKS = {
    "markdown": {"name": "Markdownlint Default", "task_type": "markdown"},
    "python": {"name": "Python Correctness", "task_type": "python"},
    "java": {"name": "Java Correctness", "task_type": "java"},
    "unsolvable": {"name": "Unsolvable Recognition", "task_type": "unsolvable"},
}


def _get_or_create_canonical_task(task_type: str) -> str:
    """Look up an existing task by name, or create it once if it doesn't exist.

    Returns the task_id for use with create_task_run().
    """
    name = CANONICAL_TASKS[task_type]["name"]
    existing = src.task_manager.get_tasks()
    for t in existing:
        if t["name"] == name and t["task_type"] == task_type:
            return t["task_id"]
    task = src.task_manager.create_task(name=name, task_type=task_type, prompt="")
    return task["task_id"]


def _validate_speed_tests(speed_tests: list) -> list[str]:
    """Validate and normalize the speed_tests list.

    - Reject unknown names
    - Deduplicate deterministically (preserve first occurrence order)
    """
    # Check for unknown names (empty list is allowed — correctness-only runs OK)
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


def _validate_correctness_tests(correctness_tests: list) -> list[str]:
    """Validate and normalize the correctness_tests list.

    - Reject unknown names
    - Deduplicate deterministically (preserve first occurrence order)
    - Empty list is allowed (speed-only runs are valid)
    """
    # Check for unknown names
    for name in correctness_tests:
        if name not in CORRECTNESS_TESTS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown correctness test: {name!r}",
            )

    # Deduplicate preserving first occurrence order
    seen: set[str] = set()
    deduped: list[str] = []
    for name in correctness_tests:
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

_CORRECTNESS_LABELS = {
    "markdown": "Markdownlint Default",
    "python": "Python Correctness",
    "java": "Java Correctness",
    "unsolvable": "Unsolvable Recognition",
}


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/api/evaluation/run")
async def run_evaluation_endpoint(config: dict):
    """Run selected speed + correctness tests and return results.

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
        "speed_tests": ["small", "medium", "large"],
        "correctness_tests": ["markdown", "python"]
    }
    """
    # --- Validation ---
    speed_tests_raw = config.get("speed_tests", [])
    speed_tests = _validate_speed_tests(speed_tests_raw)

    correctness_tests_raw = config.get("correctness_tests", [])
    correctness_tests = _validate_correctness_tests(correctness_tests_raw)

    # At least one test must be selected (speed or correctness)
    if not speed_tests and not correctness_tests:
        raise HTTPException(
            status_code=400,
            detail="At least one speed test or correctness test must be selected",
        )

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
        if max_tokens < 1 or max_tokens > 10000000:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="max_output_tokens must be an integer between 1 and 10000000",
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

    # Speed tests use a fixed output budget (1024 tokens) regardless of UI setting.
    # Correctness tests use the user-selected max_output_tokens value.
    SPEED_OUTPUT_TOKENS = 1024

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

        # Run benchmark engine (speed tests use fixed 1024 output budget)
        from src.benchmark import run_benchmark

        try:
            benchmark_result = await run_benchmark(
                lm_studio_url=lm_studio_url,
                model=model,
                prompt=prompt_text,
                iterations=iterations,
                max_tokens=SPEED_OUTPUT_TOKENS,
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

    # --- Run selected correctness tests sequentially ---
    correctness_results: list[dict] = []
    correctness_scores: list[float] = []

    total_correctness_tests = len(correctness_tests)
    for idx, test_name in enumerate(correctness_tests):
        # Get or create canonical task_id once per evaluation run
        canonical_task_id = _get_or_create_canonical_task(test_name)

        if test_name == "markdown":
            from src.task_markdown import run_markdown_task

            try:
                md_result = await run_markdown_task(
                    lm_studio_url=lm_studio_url,
                    model=model,
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                    hardware_label=hardware_label,
                    execution_environment=execution_environment,
                    connection_type=connection_type,
                )
            except Exception as e:
                logger.error("Markdown correctness failed: %s — %s", type(e).__name__, e)
                raise HTTPException(
                    status_code=502,
                    detail=f"Markdown correctness failed: {e}",
                )

            # Persist to task history via create_task_run
            src.task_manager.create_task_run(
                task_id=canonical_task_id,
                task_name=md_result["task_name"],
                task_type=md_result["task_type"],
                model=model,
                timestamp=datetime.now(timezone.utc).isoformat(),
                passed=md_result["passed"],
                score=md_result["score"],
                initial_errors=md_result["initial_errors"],
                final_errors=md_result["final_errors"],
                errors_fixed=md_result["errors_fixed"],
                output_tokens=md_result["output_tokens"],
                input_tokens=md_result["input_tokens"],
                tokens_per_second=md_result["tokens_per_second"],
                ttft_seconds=md_result.get("ttft_seconds"),
                wall_time_seconds=md_result["wall_time_seconds"],
                result=md_result,
            )

            correctness_results.append({
                "test_type": "markdown",
                "test_label": _CORRECTNESS_LABELS["markdown"],
                "score": md_result["score"],
                "passed": md_result["passed"],
                "initial_errors": md_result["initial_errors"],
                "final_errors": md_result["final_errors"],
                "errors_fixed": md_result["errors_fixed"],
                "tokens_per_second": md_result["tokens_per_second"],
                "ttft_seconds": md_result.get("ttft_seconds"),
                "wall_time_seconds": md_result["wall_time_seconds"],
                "output_tokens": md_result["output_tokens"],
                "input_tokens": md_result["input_tokens"],
                "corrected_violations": md_result.get("corrected_violations", []),
                "failure_reason": md_result.get("failure_reason"),
            })
            correctness_scores.append(md_result["score"])

            # Delay before next correctness task (skip after last)
            if idx < total_correctness_tests - 1:
                await asyncio.sleep(3)

        elif test_name == "python":
            from src.task_python import run_python_correctness_task

            try:
                py_result = await run_python_correctness_task(
                    lm_studio_url=lm_studio_url,
                    model=model,
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                    hardware_label=hardware_label,
                    execution_environment=execution_environment,
                    connection_type=connection_type,
                )
            except Exception as e:
                logger.error("Python correctness failed: %s — %s", type(e).__name__, e)
                raise HTTPException(
                    status_code=502,
                    detail=f"Python correctness failed: {e}",
                )

            # Persist to task history via create_task_run
            src.task_manager.create_task_run(
                task_id=canonical_task_id,
                task_name=py_result["task_name"],
                task_type=py_result["task_type"],
                model=model,
                timestamp=datetime.now(timezone.utc).isoformat(),
                passed=py_result["passed"],
                score=py_result["score"],
                output_tokens=py_result["output_tokens"],
                input_tokens=py_result["input_tokens"],
                tokens_per_second=py_result["tokens_per_second"],
                ttft_seconds=py_result.get("ttft_seconds"),
                wall_time_seconds=py_result["wall_time_seconds"],
                result=py_result,
            )

            correctness_results.append({
                "test_type": "python",
                "test_label": _CORRECTNESS_LABELS["python"],
                "score": py_result["score"],
                "passed": py_result["passed"],
                "total_tests": py_result["total_tests"],
                "passed_tests": py_result["passed_tests"],
                "failed_tests": py_result["failed_tests"],
                "tokens_per_second": py_result["tokens_per_second"],
                "ttft_seconds": py_result.get("ttft_seconds"),
                "wall_time_seconds": py_result["wall_time_seconds"],
                "output_tokens": py_result["output_tokens"],
                "input_tokens": py_result["input_tokens"],
            })
            correctness_scores.append(py_result["score"])

            # Delay before next correctness task (skip after last)
            if idx < total_correctness_tests - 1:
                await asyncio.sleep(3)

        elif test_name == "java":
            from src.task_java import run_java_correctness_task

            try:
                java_result = await run_java_correctness_task(
                    lm_studio_url=lm_studio_url,
                    model=model,
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                    hardware_label=hardware_label,
                    connection_type=connection_type,
                )
            except Exception as e:
                logger.error("Java correctness failed: %s — %s", type(e).__name__, e)
                raise HTTPException(
                    status_code=502,
                    detail=f"Java correctness failed: {e}",
                )

            # Persist to task history via create_task_run
            src.task_manager.create_task_run(
                task_id=canonical_task_id,
                task_name=java_result.task_name,
                task_type=java_result.task_type,
                model=model,
                timestamp=datetime.now(timezone.utc).isoformat(),
                passed=java_result.passed,
                score=java_result.score,
                output_tokens=java_result.output_tokens,
                input_tokens=java_result.input_tokens,
                tokens_per_second=java_result.tokens_per_second,
                ttft_seconds=java_result.ttft_seconds,
                wall_time_seconds=java_result.wall_time_seconds,
                result=java_result.to_dict(),
            )

            correctness_results.append({
                "test_type": "java",
                "test_label": _CORRECTNESS_LABELS["java"],
                "score": java_result.score,
                "passed": java_result.passed,
                "total_tests": java_result.total_tests,
                "passed_tests": java_result.passed_tests,
                "failed_tests": java_result.failed_tests,
                "compile_success": java_result.compile_success,
                "tokens_per_second": java_result.tokens_per_second,
                "ttft_seconds": java_result.ttft_seconds,
                "wall_time_seconds": java_result.wall_time_seconds,
                "output_tokens": java_result.output_tokens,
                "input_tokens": java_result.input_tokens,
            })
            correctness_scores.append(java_result.score)

            # Delay before next correctness task (skip after last)
            if idx < total_correctness_tests - 1:
                await asyncio.sleep(3)

        elif test_name == "unsolvable":
            from src.task_unsolvable import run_unsolvable_task

            try:
                us_result = await run_unsolvable_task(
                    lm_studio_url=lm_studio_url,
                    model=model,
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                    hardware_label=hardware_label,
                    connection_type=connection_type,
                )
            except Exception as e:
                logger.error("Unsolvable correctness failed: %s — %s", type(e).__name__, e)
                raise HTTPException(
                    status_code=502,
                    detail=f"Unsolvable correctness failed: {e}",
                )

            # Persist to task history via create_task_run
            src.task_manager.create_task_run(
                task_id=canonical_task_id,
                task_name=us_result.task_name,
                task_type=us_result.task_type,
                model=model,
                timestamp=datetime.now(timezone.utc).isoformat(),
                passed=us_result.passed,
                score=us_result.score,
                output_tokens=us_result.output_tokens,
                input_tokens=us_result.input_tokens,
                tokens_per_second=us_result.tokens_per_second,
                ttft_seconds=us_result.ttft_seconds,
                wall_time_seconds=us_result.wall_time_seconds,
                result=us_result.to_dict(),
            )

            correctness_results.append({
                "test_type": "unsolvable",
                "test_label": _CORRECTNESS_LABELS["unsolvable"],
                "score": us_result.score,
                "passed": us_result.passed,
                "impossible_detected": us_result.impossible_detected,
                "classification": us_result.classification,
                "conflict_ids": sorted(us_result.conflict_ids),
                "explanation_valid": us_result.explanation_valid,
                "tokens_per_second": us_result.tokens_per_second,
                "ttft_seconds": us_result.ttft_seconds,
                "wall_time_seconds": us_result.wall_time_seconds,
                "output_tokens": us_result.output_tokens,
                "input_tokens": us_result.input_tokens,
                "generated_response": us_result.generated_response,
            })
            correctness_scores.append(us_result.score)

            # Delay before next correctness task (skip after last)
            if idx < total_correctness_tests - 1:
                await asyncio.sleep(3)

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

    # Compute correctness score (average of selected correctness scores)
    correctness_score = None
    if correctness_scores:
        valid_scores = [s for s in correctness_scores if s is not None]
        if valid_scores:
            correctness_score = round(sum(valid_scores) / len(valid_scores), 4)

    return {
        "status": "completed",
        "model": model,
        "speed_results": speed_results,
        "correctness_results": correctness_results,
        "summary": {
            "speed_tests_run": len(speed_results),
            "correctness_tests_run": len(correctness_results),
            "correctness_score": correctness_score,
            "avg_tokens_per_second": avg_tps,
            "avg_ttft_seconds": avg_ttft,
            "total_wall_time_seconds": total_wall_time,
        },
    }
