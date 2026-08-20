"""Benchmark engine using LM Studio's native v1 API."""

import time
import uuid
from datetime import datetime, timezone

import httpx

# LM Studio native v1 API chat endpoint
CHAT_ENDPOINT = "/api/v1/chat"
MODELS_ENDPOINT = "/api/v1/models"


async def fetch_models(lm_studio_url: str) -> list[dict]:
    """Fetch available LLM models from LM Studio native v1 API.

    Parses the top-level 'models' array from LM Studio's response.
    Includes only entries where type == 'llm'.
    Uses 'key' as the model identifier and 'display_name' as the human-readable name.
    """
    url = f"{lm_studio_url}{MODELS_ENDPOINT}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    # LM Studio wraps models in a top-level "models" key
    model_list = data.get("models", data) if isinstance(data, dict) else data

    models = []
    for m in model_list:
        if m.get("type") == "llm":
            models.append({
                "key": m["key"],
                "name": m.get("display_name", m.get("name", m["key"])),
                "type": m.get("type", "llm"),
                "quantization": m.get("quantization", ""),
                "loaded": m.get("loaded", False),
            })
    return models


async def resolve_model_quantization(lm_studio_url: str, model_key: str) -> str:
    """Resolve the exact quantization string for a selected model key from the live LM Studio registry.

    Returns an empty string when the model is unknown or the registry does not expose a
    quantization value. Never infers or parses quantization from the model name.
    """
    try:
        models = await fetch_models(lm_studio_url)
    except Exception:
        return ""

    for m in models:
        if m.get("key") == model_key:
            q = m.get("quantization", "")
            if isinstance(q, str):
                return q or ""
            if isinstance(q, dict):
                name = q.get("name") or q.get("display_name")
                return str(name) if name else ""
            if q:
                return str(q)
    return ""


async def run_benchmark(
    lm_studio_url: str,
    model: str,
    prompt: str,
    iterations: int,
    max_tokens: int,
    temperature: float,
    hardware_label: str = "",
    execution_environment: str = "Local",
    connection_type: str = "",
    prompt_name: str = "",
    model_quantization: str = "",
) -> dict:
    """Run benchmark against LM Studio's /api/v1/chat endpoint.

    Uses stream=False and reads stats from the response body.

    Args:
        lm_studio_url: LM Studio server URL.
        model: Model key/identifier.
        prompt: Benchmark prompt text.
        iterations: Number of benchmark iterations.
        max_tokens: Maximum output tokens.
        temperature: Sampling temperature.
        hardware_label: Optional user-provided hardware label.
        execution_environment: Local / Self-hosted / Cloud.
        connection_type: Local network / Remote connection (for self-hosted).
        prompt_name: Optional prompt identifier/name.

    Returns a dict with:
        - run_id: str (UUID for this benchmark run)
        - timestamp: str (ISO-8601)
        - model: str
        - hardware_label: str
        - execution_environment: str
        - connection_type: str
        - prompt_name: str
        - iterations: int
        - runs: list[dict]  (per-iteration results)
        - aggregate: dict    (avg/min/max tokens/sec)
        - warm_aggregate: dict (warm-only avg tokens/sec and TTFT)
    """
    url = f"{lm_studio_url}{CHAT_ENDPOINT}"
    payload = {
        "model": model,
        "input": prompt,
        "temperature": temperature,
        "max_output_tokens": max_tokens,
        "stream": False,
        "store": False,
    }

    runs: list[dict] = []
    run_id = str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=300.0) as client:
        for i in range(1, iterations + 1):
            start = time.perf_counter()
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            elapsed = time.perf_counter() - start

            body = resp.json()
            stats = body.get("stats", {})

            # Classification
            cold_or_warm = "cold" if i == 1 else "warm"

            run_result = {
                "iteration": i,
                "cold_or_warm": cold_or_warm,
                "tokens_per_second": stats.get("tokens_per_second", 0),
                "ttft_seconds": stats.get("time_to_first_token_seconds", 0),
                "input_tokens": stats.get("input_tokens", 0),
                "output_tokens": stats.get("total_output_tokens", 0),
                "model_load_time_seconds": stats.get("model_load_time_seconds", None),
                "wall_time_seconds": round(elapsed, 4),
                "model_quantization": model_quantization or "",
            }
            runs.append(run_result)

    # Compute overall aggregate (all iterations)
    tps_values = [r["tokens_per_second"] for r in runs if r["tokens_per_second"] > 0]
    if tps_values:
        aggregate = {
            "avg_tokens_per_second": round(sum(tps_values) / len(tps_values), 2),
            "min_tokens_per_second": round(min(tps_values), 2),
            "max_tokens_per_second": round(max(tps_values), 2),
        }
    else:
        aggregate = {
            "avg_tokens_per_second": 0,
            "min_tokens_per_second": 0,
            "max_tokens_per_second": 0,
        }

    # Compute warm aggregate (exclude iteration 1)
    warm_tps = [r["tokens_per_second"] for r in runs if r["cold_or_warm"] == "warm" and r["tokens_per_second"] > 0]
    warm_ttfts = [r["ttft_seconds"] for r in runs if r["cold_or_warm"] == "warm"]

    if warm_tps:
        warm_aggregate = {
            "avg_tokens_per_second": round(sum(warm_tps) / len(warm_tps), 2),
            "avg_ttft": round(sum(warm_ttfts) / len(warm_ttfts), 2) if warm_ttfts else None,
            "available": True,
        }
    else:
        warm_aggregate = {
            "avg_tokens_per_second": None,
            "avg_ttft": None,
            "available": False,
        }

    return {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "hardware_label": hardware_label,
        "execution_environment": execution_environment,
        "connection_type": connection_type,
        "prompt_name": prompt_name,
        "iterations": iterations,
        "runs": runs,
        "aggregate": aggregate,
        "warm_aggregate": warm_aggregate,
    }