"""Benchmark routes for Solo Dev LLM Bench."""

from fastapi import APIRouter

import src.app_state

router = APIRouter()


def _get_results_store():
    """Get the current results_store from app_state module."""
    return src.app_state.results_store


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