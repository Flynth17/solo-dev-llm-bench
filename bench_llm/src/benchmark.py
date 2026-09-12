"""Benchmark engine using LM Studio's native v1 API."""

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from src.results import normalize_loaded_instance_config

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


# Resolution statuses. These distinguish the failure modes so they are never
# silently conflated into a single (misleading) quantization value downstream.
QUANT_FOUND = "found"          # model present, usable quantization value available
QUANT_ABSENT = "absent"        # model present but registry exposes no quantization metadata
QUANT_MALFORMED = "malformed"  # quantization field present but not a usable value
QUANT_NOT_FOUND = "not_found"  # model key absent from the registry
QUANT_ERROR = "error"          # provider/lookup failure (network, HTTP, malformed JSON)

# Persisted label for each non-FOUND status in the model_quantization column.
# FOUND uses its resolved value verbatim (see persisted_quantization).
_QUANT_PERSIST_LABELS = {
    QUANT_ABSENT: "metadata_absent",
    QUANT_MALFORMED: "metadata_malformed",
    QUANT_NOT_FOUND: "model_not_found",
    QUANT_ERROR: "lookup_failed",
}


@dataclass(frozen=True)
class QuantizationStatus:
    """Result of resolving a model's exact quantization from the live registry."""

    status: str
    value: str = ""
    detail: str = ""


async def _resolve_quantization_status(lm_studio_url: str, model_key: str) -> QuantizationStatus:
    """Resolve the exact quantization for a model key, distinguishing failure modes."""
    try:
        models = await fetch_models(lm_studio_url)
    except Exception as exc:
        return QuantizationStatus(
            status=QUANT_ERROR, value="", detail=f"{type(exc).__name__}: {exc}"
        )

    for m in models:
        if m.get("key") == model_key:
            q = m.get("quantization", "")
            if isinstance(q, str):
                if q:
                    return QuantizationStatus(status=QUANT_FOUND, value=q)
                return QuantizationStatus(status=QUANT_ABSENT, value="")
            if isinstance(q, dict):
                name = q.get("name") or q.get("display_name")
                if name:
                    return QuantizationStatus(status=QUANT_FOUND, value=str(name))
                return QuantizationStatus(
                    status=QUANT_MALFORMED,
                    value="",
                    detail="quantization dict without name/display_name",
                )
            return QuantizationStatus(
                status=QUANT_MALFORMED,
                value="",
                detail=f"unexpected quantization type: {type(q).__name__}",
            )

    return QuantizationStatus(
        status=QUANT_NOT_FOUND, value="", detail=f"model key {model_key!r} not in registry"
    )


def persisted_quantization(status: QuantizationStatus) -> str:
    """Map a resolution status to the value to persist in model_quantization.

    FOUND returns the resolved value verbatim; every failure mode maps to a stable,
    non-fabricated label so states are not conflated into a misleading value.
    """
    if status.status == QUANT_FOUND:
        return status.value or ""
    return _QUANT_PERSIST_LABELS.get(status.status, "lookup_failed")


async def resolve_persisted_quantization(lm_studio_url: str, model_key: str) -> str:
    """Resolve quantization and return the value to persist (distinguishing failure modes)."""
    return persisted_quantization(await _resolve_quantization_status(lm_studio_url, model_key))


async def resolve_model_quantization(lm_studio_url: str, model_key: str) -> str:
    """Backward-compatible wrapper returning the raw resolved quantization string or ''.

    Prefer :func:`resolve_persisted_quantization`, which distinguishes failure modes.
    """
    return (await _resolve_quantization_status(lm_studio_url, model_key)).value


async def resolve_context_capacity(lm_studio_url: str, model: str) -> dict[str, int | None]:
    """Resolve the configured maximum context window for a model.

    Returns ``{"model_max_context": ..., "loaded_context": ...}`` where each value
    is an int or ``None``.  Strictly best-effort and never fabricated: on any
    failure (404, network error, malformed JSON) or when the field is absent we
    return ``None`` rather than assume a default context size.

    Reliable configured-max source in this environment's LM Studio API is
    ``max_context_length`` on ``GET /api/v1/models`` — the same registry endpoint
    already fetched for quantization. ``loaded_context`` is intentionally left
    ``None``: no reliable API field exposes the currently-loaded context size, and
    inferring it from prompt tokens is not permitted. Do not substitute an assumed
    default (see Act 7 notes).
    """
    result: dict[str, int | None] = {"model_max_context": None, "loaded_context": None}
    try:
        url = f"{lm_studio_url}{MODELS_ENDPOINT}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return result
            data = resp.json()
    except Exception:
        # Any failure (connection error, malformed JSON, non-JSON body) -> unavailable.
        return result

    models = data.get("models", data) if isinstance(data, dict) else []
    for m in models:
        if not isinstance(m, dict):
            continue
        if m.get("key") == model:
            ctx = m.get("max_context_length")
            if isinstance(ctx, int) and ctx > 0:
                # Configured maximum context window (reliable machine-readable source).
                result["model_max_context"] = ctx
            break
    return result


async def resolve_loaded_instance_config(lm_studio_url: str, model: str) -> dict:
    """Resolve the loaded-instance inference configuration for ``model`` (Act 11B).

    Best-effort: reads ``GET /api/v1/models`` once and normalizes the matching
    model's first loaded instance into first-class fields via
    :func:`src.results.normalize_loaded_instance_config`. Returns defaults with
    ``reasoning_mode = off`` and KV cache quantization recorded as unknown on any
    failure, or when the model has no discoverable loaded instance. Never fabricates.
    """
    try:
        url = f"{lm_studio_url}{MODELS_ENDPOINT}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return normalize_loaded_instance_config({})
            data = resp.json()
    except Exception:
        # Any failure (connection error, malformed JSON, non-JSON body) -> unavailable.
        return normalize_loaded_instance_config({})

    models = data.get("models", data) if isinstance(data, dict) else []
    for m in models:
        if not isinstance(m, dict):
            continue
        instances = m.get("loaded_instances") or []
        if not isinstance(instances, list) or not instances:
            continue
        # Prefer an instance whose id matches the model key; else first instance of a loaded model.
        candidate = next(
            (it for it in instances if isinstance(it, dict) and it.get("id") == model),
            None,
        )
        if candidate is None and m.get("key") == model:
            candidate = instances[0]
        if candidate is not None:
            return normalize_loaded_instance_config(candidate)

    # Model has no discoverable loaded instance -> defaults, KV recorded unknown.
    return normalize_loaded_instance_config({})


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
        - benchmark_duration_seconds: float (total elapsed across every request of this invocation)
        - runs: list[dict]  (per-iteration results)
        - aggregate: dict    (avg/min/max tokens/sec)
        - warm_aggregate: dict (warm-only avg tokens/sec and TTFT)
    """
    url = f"{lm_studio_url}{CHAT_ENDPOINT}"
    # Canonical benchmark rule: reasoning is explicitly OFF for benchmark requests
    # rather than inherited from the model's default. This keeps throughput/timing
    # metrics free of reasoning/thinking tokens. Other generation params are
    # unchanged. (The LM Studio registry may report a per-model reasoning default
    # such as "on"; that is intentionally ignored here.)
    payload = {
        "model": model,
        "input": prompt,
        "temperature": temperature,
        "max_output_tokens": max_tokens,
        "stream": False,
        "store": False,
        "reasoning": "off",
    }

    runs: list[dict] = []
    run_id = str(uuid.uuid4())

    # Total elapsed across every request of this benchmark invocation. Distinct from
    # per-iteration wall_time_seconds, which measures a single request.
    loop_start = time.perf_counter()
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

    benchmark_duration_seconds = round(time.perf_counter() - loop_start, 4)

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
        "benchmark_duration_seconds": benchmark_duration_seconds,
        "runs": runs,
        "aggregate": aggregate,
        "warm_aggregate": warm_aggregate,
    }