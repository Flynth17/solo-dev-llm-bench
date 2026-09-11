"""Benchmark routes for Solo Dev LLM Bench."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

import src.app_state
from src.benchmark import (
    run_benchmark,
    resolve_persisted_quantization,
    resolve_context_capacity,
)
from src.hardware import snapshot_hardware
from src.telemetry import TelemetrySampler, TELEMETRY_SAMPLE_INTERVAL

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
        if max_tokens < 1 or max_tokens > 10000000:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="max_tokens must be an integer between 1 and 10000000")

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

    # Act 8: runtime utilisation telemetry for this invocation. The sampler starts
    # before the benchmark iterations and is always stopped afterwards (a finally block
    # guarantees no background thread leaks on failure). Values are captured once per
    # invocation and attached to every row below.
    telemetry = TelemetrySampler(sample_interval=TELEMETRY_SAMPLE_INTERVAL)
    telemetry.start()
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
    finally:
        telemetry_dict = telemetry.stop()

    # Persist each iteration as a separate CSV row
    run_id = benchmark_result["run_id"]
    timestamp = benchmark_result["timestamp"]
    model_key = benchmark_result["model"]
    model_display_name = benchmark_result.get("model", model_key)

    # Resolve exact quantization for the selected model from the live registry,
    # distinguishing failure modes so they are not conflated in stored results.
    model_quantization = await resolve_persisted_quantization(lm_studio_url, model_key)

    # Act 7: capture machine snapshot + context capacity ONCE per invocation,
    # then reuse across all iteration rows (stable fields, not recomputed per row).
    hardware_snapshot = snapshot_hardware()
    context_capacity = await resolve_context_capacity(lm_studio_url, model)

    results_store = _get_results_store()
    for run in benchmark_result["runs"]:
        row = {
            "timestamp": timestamp,
            "run_id": run_id,
            "model_key": model_key,
            "model_display_name": model_display_name,
            "model_quantization": model_quantization,
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
            # Total elapsed across every request of this invocation. Identical value
            # on each row (mirrors how run_id/timestamp are shared); distinct from the
            # per-request wall_time_seconds above.
            "benchmark_duration_seconds": benchmark_result["benchmark_duration_seconds"],
            "prompt_name": prompt_name,
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            # --- Act 7: reproducible / comparable run metadata ---
            # Context fields are kept distinct (never conflated):
            #   prompt_tokens      = actual live context used by the request
            #   model_max_context    = configured model max context (None if unavailable)
            #   loaded_context       = loaded/n_ctx of running model  (None if unavailable)
            "prompt_tokens": run["input_tokens"],
            "model_max_context": context_capacity.get("model_max_context"),
            "loaded_context": context_capacity.get("loaded_context"),
            # Machine / environment snapshot (stable fields; None when unavailable).
            "cpu_model": hardware_snapshot.get("cpu_model"),
            "cpu_logical_cores": hardware_snapshot.get("cpu_logical_cores"),
            "cpu_physical_cores": hardware_snapshot.get("cpu_physical_cores"),
            "installed_ram_bytes": hardware_snapshot.get("installed_ram_bytes"),
            "gpu_model": hardware_snapshot.get("gpu_model"),
            "total_vram_bytes": hardware_snapshot.get("total_vram_bytes"),
            "os_platform": hardware_snapshot.get("os_platform"),
            "os_version": hardware_snapshot.get("os_version"),
            "nvidia_driver_version": hardware_snapshot.get("nvidia_driver_version"),
            "python_version": hardware_snapshot.get("python_version"),
            # --- Act 8: runtime utilisation telemetry (one value per invocation,
            #     shared across all rows) — None where a source was unavailable ---
            "system_ram_used_start_bytes": telemetry_dict.get("system_ram_used_start_bytes"),
            "system_ram_used_peak_bytes": telemetry_dict.get("system_ram_used_peak_bytes"),
            "system_ram_used_end_bytes": telemetry_dict.get("system_ram_used_end_bytes"),
            "process_rss_start_bytes": telemetry_dict.get("process_rss_start_bytes"),
            "process_rss_peak_bytes": telemetry_dict.get("process_rss_peak_bytes"),
            "process_rss_end_bytes": telemetry_dict.get("process_rss_end_bytes"),
            "vram_used_start_bytes": telemetry_dict.get("vram_used_start_bytes"),
            "vram_used_peak_bytes": telemetry_dict.get("vram_used_peak_bytes"),
            "vram_used_end_bytes": telemetry_dict.get("vram_used_end_bytes"),
            "cpu_util_avg_pct": telemetry_dict.get("cpu_util_avg_pct"),
            "cpu_util_peak_pct": telemetry_dict.get("cpu_util_peak_pct"),
            "gpu_util_avg_pct": telemetry_dict.get("gpu_util_avg_pct"),
            "gpu_util_peak_pct": telemetry_dict.get("gpu_util_peak_pct"),
            "telemetry_sample_count": telemetry_dict.get("telemetry_sample_count"),
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