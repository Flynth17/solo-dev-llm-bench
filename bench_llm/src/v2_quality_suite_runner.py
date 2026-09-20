"""Suite-isolated standalone V2 quality runner + coordinator (Act 11C-4D).

Replaces the previous *monolithic* live executor (:mod:`src.v2_quality_executor`)
with a suite-isolated architecture:

    Fresh standalone process
        -> PYTHON   -> validate 58 checks -> report -> EXIT
        -> JAVA     -> validate 52 checks -> report -> EXIT
        -> MARKDOWN -> validate 20 checks -> report -> EXIT
        -> EVIDENCE -> validate 30 checks -> report -> EXIT
        -> DRIFT    -> validate  6 checks -> report -> EXIT

    Coordinator
        -> verify same run / model / config identity
        -> combine fixed denominators
        -> final total = 166

Design rules honoured here (no frozen corpus / persistence / UI / LM Studio
touches):

  * **Canonical denominators are IMMUTABLE constants.** ``python=58``,
    ``java=52``, ``markdown=20``, ``evidence=30``, ``drift=6``; total ``166``.
    They are never derived from returned :class:`~src.quality._common.CaseResult`
    rows, so a failure can never *remove* checks (no ``0/0``), *multiply* them
    (Java compile failure cannot turn 52 into 64), or otherwise move the
    denominator. The accounting function caps passed units onto the fixed
    denominator; the returned total is always the canonical constant for the
    suite, and the aggregate is always ``D/D`` per suite / ``166`` total.

  * **Fresh standalone process per suite.** :func:`run_v2_run` spawns each suite
    as its own OS process (``python -m src v2-suite <suite>``) so no benchmark
    state leaks between suites and the Pi Web session history is never used as
    benchmark context.

  * **Existing direct chat path.** Every model request uses the canonical
    ``/api/v1/chat`` endpoint with the identical payload the live executor used.
    The reasoning policy stays ``inherit`` -- the payload carries **no**
    ``reasoning`` and **no** ``reasoning_effort`` key, ever.

  * **Read-only config identity.** Before every suite the coordinator probes the
    loaded model/config (``GET /api/v1/models``) to confirm the expected model is
    still loaded and to compute a configuration fingerprint; the same helper is
    reused inside each suite so all five suites key on an identical fingerprint.

  * **No persistence.** Results are returned in memory or written to temporary
    JSON for coordinator handoff only. Nothing touches SQLite / CSV / the Results
    UI / legacy scoring.

This module does not launch a live benchmark on its own; the HTTP transport is
injectable so the plumbing can be proven fully offline (see the test suite).
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import httpx

# --- Reuse verbatim: frozen validators + extractors (no mutation of corpus). ---
from src.v2_quality_extractors import (
    extract_python_module,
    extract_java_solution,
    extract_markdown_document,
    extract_evidence_response,
    extract_drift_response,
)
from src.quality import python_cases as _py
from src.quality import java_cases as _java
from src.quality import markdown_cases as _md
from src.quality import evidence_cases as _ev
from src.quality import drift_cases as _drift
from src.quality.java_cases import _run_case_test as _java_run_case_test

# --- Reuse read-only: canonical prompt builders + block renderer (no mutation). ---
from src.v2_quality_executor import (
    _python_prompt,
    _java_prompt,
    _markdown_prompt,
    _evidence_prompt,
    _drift_prompt,
    _evidence_block,
)
from src.v2_quality_prompts import PYTHON_SPECS, JAVA_SPECS, evidence_scenarios

# --- Reuse read-only: Act 11B configuration-identity helpers (no I/O at import). ---
from src.benchmark import (
    resolve_persisted_quantization,
    resolve_context_capacity,
    resolve_loaded_instance_config,
)
from src.results import compute_configuration_fingerprint, classify_run_for_result
# Run-level execution provenance (Act: reproducibility). Stdlib-only capture module;
# importing it cannot introduce benchmark-execution side effects.
from src.provenance import capture_provenance as _capture_provenance

# Chat endpoint path on the LM Studio base URL (identical to the live executor).
CHAT_ENDPOINT = "/api/v1/chat"

# ---------------------------------------------------------------------------
# Immutable canonical denominators (the whole point of this layer).
# ---------------------------------------------------------------------------

CANONICAL_DENOMINATORS: dict[str, int] = {
    "python": 58,
    "java": 52,
    "markdown": 20,
    "evidence": 30,
    "drift": 6,
}
TOTAL_DENOMINATOR: int = sum(CANONICAL_DENOMINATORS.values())  # == 166
SUITES: tuple[str, ...] = ("python", "java", "markdown", "evidence", "drift")

# Number of model requests each suite makes (identical to the canonical corpus).
REQUEST_COUNTS: dict[str, int] = {
    "python": 1,
    "java": len(JAVA_SPECS),
    "markdown": 1,
    "evidence": len(evidence_scenarios()),
    "drift": 1,
}

# Overridable via env so the coordinator can point every child at the same model.
DEFAULT_LM_STUDIO_URL = os.environ.get("BENCH_LM_STUDIO_URL", "http://127.0.0.1:1234")
DEFAULT_MODEL = os.environ.get("BENCH_V2_MODEL", "ornith-1.5-35b-a3b")

# The request payload carries NO reasoning / reasoning_effort -- policy = inherit.
REASONING_POLICY = "inherit"

# --- Fixed output-ceiling policy for the V2 QUALITY benchmark. ---
#
# This is an OUTPUT CEILING, not a target generation length. The quality corpus prompts
# are intentionally small, so we cap generated tokens at a fixed fraction of the
# effective context window and leave the remaining headroom implicit.
#
#   effective_context_capacity = min(model_max_context, loaded_context)      # when known
#   requested_max_output_tokens = effective_context_capacity * 75 // 100
#
# We deliberately do NOT estimate or subtract prompt tokens: no chars/token heuristic,
# no tokenizer library. Prompt-aware budgeting belongs to the separate long-context
# benchmark, which uses its own policy later. If neither context value is known we raise
# -- never fabricate a capacity and never fall back to a hardcoded token count.
OUTPUT_BUDGET_POLICY: str = "75_percent_context"
OUTPUT_BUDGET_PERCENT: int = 75


def _resolve_effective_capacity(model_max_context: Optional[int],
                                loaded_context: Optional[int]) -> Optional[int]:
    """Return ``min(known model_max_context, known loaded_context)``.

    If both are known this is their min; if only one is known it wins as-is; if neither
    is known there is no ceiling (``None``). This intentionally never fabricates a
    context size: any ``None``/non-positive input is simply ignored rather than assumed.
    """
    known = [c for c in (model_max_context, loaded_context)
             if isinstance(c, int) and not isinstance(c, bool) and c > 0]
    return min(known) if known else None


def output_ceiling(model_max_context: Optional[int], loaded_context: Optional[int]) -> int:
    """Return the fixed output ceiling for the V2 QUALITY benchmark.

    ``floor(effective_context_capacity * OUTPUT_BUDGET_PERCENT / 100)`` where effective
    capacity is :func:`_resolve_effective_capacity`. Raises
    :class:`ConfigurationMismatchError` when neither context value is known -- we never
    fabricate a capacity or fall back to a hardcoded token count. Integer arithmetic
    keeps the policy exact and reproducible.
    """
    effective = _resolve_effective_capacity(model_max_context, loaded_context)
    if effective is None:
        raise ConfigurationMismatchError(
            f"cannot apply output budget {OUTPUT_BUDGET_POLICY!r}: neither model_max_context "
            "nor loaded_context is resolvable (min(model_max_context, loaded_context) unknown)"
        )
    return effective * OUTPUT_BUDGET_PERCENT // 100


# ---------------------------------------------------------------------------
# Fixed-denominator accounting (pure; the heart of this layer).
# ---------------------------------------------------------------------------

def account_checks(suite: str, case_results: list[Any]) -> tuple[int, int]:
    """Return ``(checks_passed, checks_total)`` for *suite* onto its fixed
    canonical denominator.

    * ``checks_total`` is **always** the immutable constant
      ``CANONICAL_DENOMINATORS[suite]`` -- it is never derived from
      ``case_results``, can never be ``0``, and can never be inflated, whatever
      happened during the run. There is no ``collapse`` path that discards real
      results: a partial validation must not zero the whole suite.
    * ``checks_passed`` is the count of passed check-units emitted by the frozen
      validators, **capped** at the canonical denominator so a validator that emits
      more rows than canonical can never push the score above ``D``. Per-case
      failures (a Java compile failure, one evidence scenario dropped) simply emit
      fewer passing units; the denominator is untouched and cannot exceed ``D``.

    The two behaviours below fall out of these rules without any special-casing:

    * Successful extraction + partial validation preserves actual passed checks --
      markdown 19/20 stays 19/20, drift 4/6 stays 4/6.
    * An extraction failure leaves ``case_results`` empty, so the sum is ``0`` and
      the score is ``0 / D`` (e.g. python 0/58) rather than a meaningless ``0/0``.

    This is the single place denominators are decided -- nowhere else in this
    layer may a canonical constant be derived from validator output.
    """
    if suite not in CANONICAL_DENOMINATORS:
        raise KeyError(f"unknown suite {suite!r}; cannot assign a canonical denominator")
    total = CANONICAL_DENOMINATORS[suite]
    passed = sum(getattr(r, "checks_passed", 0) for r in case_results)
    return min(passed, total), total


# ---------------------------------------------------------------------------
# Structured per-suite result (coordinator handoff; serialisable to JSON).
# ---------------------------------------------------------------------------

@dataclass
class SuiteResult:
    """Small, structured, JSON-serialisable outcome of one standalone suite run.

    Carries everything the coordinator needs for identity + denominator checks and
    everything a reporter needs for wall-time / telemetry / failures. ``checks_passed``
    / ``checks_total`` are already normalised onto the fixed canonical denominator by
    :func:`account_checks`.
    """

    run_id: str
    suite: str
    model_identifier: str
    configuration_fingerprint: str
    classification: str
    reasoning_policy: str
    loaded_reasoning_mode: str
    requests_expected: int
    requests_completed: int
    checks_passed: int
    checks_total: int
    extraction_failures: list[dict[str, Any]] = field(default_factory=list)
    validation_failures: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    wall_time_seconds: float = 0.0
    ttft_seconds: Optional[float] = None
    prefill_throughput: Optional[float] = None
    decode_throughput: Optional[float] = None

    # --- Extra diagnostics (not required by the coordinator, useful for reporting). ---
    lm_studio_url: str = ""
    model_max_context: Optional[int] = None
    loaded_context: Optional[int] = None
    mtp_state: str = "unknown"
    speculative_simple: bool = False
    # max_output_tokens carries the fixed output ceiling for the suite. 0 means "unset";
    # run_suite always sets it to a real computed ceiling.
    max_output_tokens: int = 0
    temperature: float = 0.0

    # --- Fixed output-ceiling budget telemetry (benchmark configuration, reproducible). ---
    # effective_context_capacity: min(known model_max_context, loaded_context).
    effective_context_capacity: Optional[int] = None
    # output_budget_policy / output_budget_percent describe the ceiling policy used.
    output_budget_policy: str = OUTPUT_BUDGET_POLICY
    output_budget_percent: int = OUTPUT_BUDGET_PERCENT
    # requested_max_output_tokens: the fixed ceiling actually sent to /api/v1/chat.
    requested_max_output_tokens: Optional[int] = None

    requests: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SuiteResult":
        known = {f.name for f in dataclasses.fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(**kwargs)


class ConfigurationMismatchError(Exception):
    """Raised by the coordinator when identity/denominator checks fail.

    Aggregation must **not** proceed on this error -- callers stop and report the
    mismatch instead of combining suites from different runs/models/configs.
    """


# ---------------------------------------------------------------------------
# Raw response -> answer text + telemetry (verbatim reuse from the live executor).
# Only the *message* segments form the semantic deliverable being graded.
# ---------------------------------------------------------------------------

def _seg_text(seg: Any) -> str:
    if not isinstance(seg, dict):
        return ""
    for key in ("content", "text"):
        value = seg.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _response_text(body: Any) -> tuple[Optional[str], Optional[bool]]:
    if not isinstance(body, dict):
        return None, None
    segments = body.get("output")
    stats = body.get("stats")
    stats = stats if isinstance(stats, dict) else {}

    def _stats_reasoning() -> Optional[bool]:
        v = stats.get("reasoning_output_tokens")
        if v is None:
            return None
        try:
            return bool(v) if not isinstance(v, bool) else bool(int(v))
        except (TypeError, ValueError):
            return None

    reasoning_present: Optional[bool] = None
    message_parts: list[str] = []
    if isinstance(segments, list):
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type")
            text = _seg_text(seg)
            if seg_type == "reasoning":
                reasoning_present = True
            elif seg_type == "message" and text:
                message_parts.append(text)
    if reasoning_present is not True:
        rp_stats = _stats_reasoning()
        if rp_stats is not None:
            reasoning_present = rp_stats
    # Contract: only type="message" segments form the graded deliverable. A
    # type="reasoning" segment is telemetry and must NEVER be used to back-fill
    # missing message content -- doing so fed generated reasoning into extractors
    # (e.g. a python response that was all reasoner output produced "multiple
    # python code blocks"). If no final message exists, answer is None and the
    # caller records a distinct extraction failure; telemetry above is preserved.
    # Preserve the final-message bytes verbatim: do NOT strip a legitimate terminal
    # newline (a Markdown document graded by MD-19 must keep its single trailing
    # newline all the way through to the frozen validator). No suite relies on this
    # normalisation -- Python/Java extractors trim internally, and Evidence/DRIFT are
    # exact pass-throughs -- so leading/trailing whitespace is preserved faithfully
    # for suite-specific extraction rather than collapsed here.
    answer = "\n".join(message_parts) if message_parts else None
    return answer, reasoning_present


def _telemetry(body: Any, duration: float) -> dict[str, Any]:
    stats = body.get("stats") if isinstance(body, dict) else {}
    if not isinstance(stats, dict):
        stats = {}

    def _int(key: str) -> int:
        v = stats.get(key)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    input_tokens = _int("input_tokens")
    total_output_tokens = _int("total_output_tokens")
    ttft = stats.get("time_to_first_token_seconds")
    try:
        ttft = float(ttft) if ttft is not None else None
    except (TypeError, ValueError):
        ttft = None
    tps = stats.get("tokens_per_second")
    try:
        decode = float(tps) if tps is not None else None
    except (TypeError, ValueError):
        decode = None
    prefill = (input_tokens / ttft) if (ttft and ttft > 0 and input_tokens > 0) else None
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": total_output_tokens,
        "reasoning_tokens": _int("reasoning_output_tokens"),
        "ttft_seconds": ttft,
        "prefill_throughput_tokens_per_second": prefill,
        "decode_throughput_tokens_per_second": decode,
    }


# ---------------------------------------------------------------------------
# Transport (real httpx) + one-retry wrapper for genuine transport/server errors.
# Identical to the live executor's chat path -- only reused, not reimplemented.
# ---------------------------------------------------------------------------

async def _httpx_chat(client: httpx.AsyncClient, url: str, payload: dict) -> Any:
    resp = await client.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            return exc.response.status_code >= 500
        except Exception:  # pragma: no cover - defensive
            return False
    return False


async def _invoke(transport: Callable[[httpx.AsyncClient, str, dict], Awaitable[Any]],
                  client: httpx.AsyncClient, url: str, payload: dict):
    start = time.perf_counter()
    attempts = 0
    while True:
        try:
            body = await transport(client, url, payload)
            return body, time.perf_counter() - start
        except BaseException as exc:
            if not _is_retryable(exc):
                raise
            attempts += 1
            if attempts >= 2:
                raise


def build_chat_payload(model: str, prompt: str, temperature: float = 0.0,
                       max_output_tokens: int = 0) -> dict:
    """Construct the canonical ``/api/v1/chat`` payload.

    ``max_output_tokens`` is supplied by the caller as the fixed output ceiling for the
    V2 QUALITY benchmark (see :func:`output_ceiling`). The default of ``0`` is only a
    sentinel -- run_suite always passes a real computed ceiling, so no hardcoded ceiling
    can silently survive in the request path.

    Deliberately carries **no** ``reasoning`` and **no** ``reasoning_effort`` key --
    policy is ``inherit`` and LM Studio runs with its loaded configuration. Tests
    assert this directly (see test #7).
    """
    return {
        "model": model,
        "input": prompt,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "stream": False,
        "store": False,
    }


# ---------------------------------------------------------------------------
# Read-only configuration probe (Act 11B helpers + registry read).
# ---------------------------------------------------------------------------

async def _probe_model_config(lm_studio_url: str, model: str) -> dict[str, Any]:
    """Resolve machine-readable model identity + configuration for *model*.

    Reuses the live executor's exact helper composition so the resulting
    ``configuration_fingerprint`` is identical between suites and with the legacy
    executor. Best-effort: every field records a real value or an honest
    failure-mode label and never fabricates.
    """
    quantization = await resolve_persisted_quantization(lm_studio_url, model)
    capacity = await resolve_context_capacity(lm_studio_url, model)
    instance = await resolve_loaded_instance_config(lm_studio_url, model)

    loaded_context = instance.get("loaded_context") or capacity.get("loaded_context")
    return {
        "model_key": model,
        "model_quantization": quantization,
        "loaded_context": loaded_context,
        "model_max_context": capacity.get("model_max_context"),
        # LM Studio's registry / loaded-instance API does not expose a loaded
        # reasoning state, and we no longer override it via the request. Record
        # ``not_exposed`` rather than inferring one (do NOT default to off/on).
        "reasoning_mode": "not_exposed",
        "flash_attention": instance.get("flash_attention"),
        "offload_kv_cache_to_gpu": instance.get("offload_kv_cache_to_gpu"),
        "eval_batch_size": instance.get("eval_batch_size"),
        "physical_batch_size": instance.get("physical_batch_size"),
        "parallel": instance.get("parallel"),
        "kv_cache_k_quantization": instance.get("kv_cache_k_quantization"),
        "kv_cache_v_quantization": instance.get("kv_cache_v_quantization"),
    }


async def verify_model_loaded(lm_studio_url: str, model: str) -> dict[str, Any]:
    """Read-only probe that the *expected* model is still loaded in LM Studio.

    Returns ``{"loaded": bool, "key", "quantization", "max_context_length",
    "speculative_draft_mtp": bool, "speculative_draft_simple": bool}``. Used by the
    coordinator before each suite to confirm identity without mutating anything.
    """
    url = f"{lm_studio_url.rstrip('/')}{CHAT_ENDPOINT}".replace(CHAT_ENDPOINT, "/api/v1/models")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"loaded": False, "key": model}
            data = resp.json()
    except Exception:
        return {"loaded": False, "key": model}

    models = data.get("models", data) if isinstance(data, dict) else []
    for m in models:
        if not isinstance(m, dict):
            continue
        key = m.get("key")
        if key != model:
            continue
        instances = m.get("loaded_instances") or []
        loaded = bool(instances)
        cfg = {}
        mtp = False
        simple = False
        if instances:
            ic = instances[0].get("config") if isinstance(instances[0], dict) else None
            if isinstance(ic, dict):
                cfg = ic
                mtp = bool(cfg.get("speculative_draft_mtp"))
                simple = bool(cfg.get("speculative_draft_simple"))
        return {
            "loaded": loaded,
            "key": key,
            "quantization": (m.get("quantization") or {}).get("name"),
            "max_context_length": m.get("max_context_length"),
            "speculative_draft_mtp": mtp,
            "speculative_draft_simple": simple,
        }
    return {"loaded": False, "key": model}


# ---------------------------------------------------------------------------
# Per-suite request assembly (reuses canonical prompt builders verbatim).
# ---------------------------------------------------------------------------

def _suite_requests(suite: str) -> list[tuple[str, str]]:
    """Return ``[(request_id, prompt), ...]`` for *suite* using the frozen prompts.

    The shapes are bit-identical to the live executor's ``_build_requests`` so the
    extractors behave exactly as before -- this layer only isolates and accounts.
    """
    if suite == "python":
        return [("PY", _python_prompt())]
    if suite == "java":
        return [(c.id, _java_prompt(c)) for c in JAVA_SPECS]
    if suite == "markdown":
        # The canonical live prompt MUST carry the frozen broken document so the
        # model has something to repair. ``_markdown_prompt`` returns
        # (instruction, broken_document); append the fixture verbatim -- we do not
        # touch the frozen markdown corpus or validator.
        instruction, broken_document = _markdown_prompt()
        return [("MD", f"{instruction}{broken_document}")]
    if suite == "evidence":
        return [(cid, _evidence_prompt(cid)) for cid in evidence_scenarios()]
    if suite == "drift":
        return [("DRIFT-01", _drift_prompt())]
    raise KeyError(f"unknown suite {suite!r}")


def _aggregate_throughput(values: list[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


# ---------------------------------------------------------------------------
# Standalone single-suite runner (one OS process per suite in practice).
# ---------------------------------------------------------------------------

async def run_suite(
    suite: str,
    lm_studio_url: str = DEFAULT_LM_STUDIO_URL,
    model: str = DEFAULT_MODEL,
    *,
    run_id: Optional[str] = None,
    temperature: float = 0.0,
    chat_transport: Optional[Callable[[httpx.AsyncClient, str, dict], Awaitable[Any]]] = None,
    model_config_override: Optional[dict[str, Any]] = None,
) -> SuiteResult:
    """Run *one* suite in isolation and return a :class:`SuiteResult`.

    ``chat_transport`` is injectable so the pipeline can be proven fully offline;
    when omitted the real ``/api/v1/chat`` POST is used. ``model_config_override``
    lets tests supply a config (and hence fingerprint) without touching LM Studio.

    Every request uses the same fixed output ceiling for the V2 QUALITY benchmark,
    :func:`output_ceiling` -- ``floor(effective_context_capacity * 75 // 100)``. The
    policy does not estimate or subtract prompt tokens; it is a pure context ceiling.
    """
    if suite not in CANONICAL_DENOMINATORS:
        raise KeyError(f"unknown suite {suite!r}")

    t0 = time.perf_counter()
    run_id = run_id or os.environ.get("BENCH_RUN_ID") or "run-local"
    transport = chat_transport or _httpx_chat
    chat_url = f"{lm_studio_url.rstrip('/')}{CHAT_ENDPOINT}"

    # --- Model identity + configuration (read-only). ---
    if model_config_override is not None:
        cfg = dict(model_config_override)
    else:
        cfg = await _probe_model_config(lm_studio_url, model)
    # The quality output-budget policy contributes to benchmark identity so that
    # old 4096 / old 90% / new 75% runs are never treated as equivalent configurations.
    fp_cfg = dict(cfg)
    fp_cfg["output_budget_policy"] = OUTPUT_BUDGET_POLICY
    fingerprint = compute_configuration_fingerprint(fp_cfg)
    classification = classify_run_for_result(cfg)
    loaded_reasoning_mode = cfg.get("reasoning_mode", "not_exposed")
    # LM Studio's registry / loaded-instance API (via resolve_loaded_instance_config)
    # does not surface the MTP/speculative draft flags, so we honestly record
    # "unknown" here rather than infer one. The coordinator-level pre-check can read
    # them directly from the registry (see verify_model_loaded); this suite result
    # records what was actually resolved.
    mtp_state = "unknown"

    # --- Fixed output-ceiling budget (context ceiling, no prompt accounting). ---
    # Computed once per suite and shared by every request; raises ConfigurationMismatchError
    # when neither context value is known -- we never fabricate a capacity or hardcoded count.
    _mmc = cfg.get("model_max_context")
    _loaded = cfg.get("loaded_context")
    effective_capacity = _resolve_effective_capacity(_mmc, _loaded)
    requested_max_output_tokens = output_ceiling(_mmc, _loaded)
    # Ceiling actually sent -- recorded on the suite result.
    suite_requested_budget: Optional[int] = requested_max_output_tokens

    requests = _suite_requests(suite)
    all_results: list[Any] = []
    extraction_failures: list[dict[str, Any]] = []
    validation_failures: list[dict[str, Any]] = []
    req_records: list[dict[str, Any]] = []

    # No per-suite "collapse" flag exists here: account_checks() decides the
    # denominator canonically. An empty all_results (extraction produced nothing)
    # naturally scores 0 / D, while a partial validation keeps its passed units.

    async with httpx.AsyncClient(timeout=300.0) as client:
        for req_id, prompt in requests:
            # Every request uses the same fixed output ceiling (see output_ceiling).
            # It was computed once per suite before the loop; raises if capacity is unknown.
            payload = build_chat_payload(model, prompt, temperature, requested_max_output_tokens)
            body, elapsed = await _invoke(transport, client, chat_url, payload)
            answer, _reasoning_present = _response_text(body)
            telem = _telemetry(body, elapsed)

            # Contract: only type="message" segments may reach extractors/
            # validators. A response with no final message (e.g. a reasoning-only
            # turn) yields no graded deliverable -- record a distinct failure and
            # never forward the reasoning to an extractor. Telemetry is preserved.
            if answer is None:
                extraction_failures.append(
                    {"request_id": req_id, "reason": "no final message content"}
                )
                req_records.append({
                    "request_id": req_id,
                    "extraction_success": False,
                    "checks_passed": 0,
                    "checks_total_validator": 0,
                    "effective_context_capacity": effective_capacity,
                    "output_budget_policy": OUTPUT_BUDGET_POLICY,
                    "output_budget_percent": OUTPUT_BUDGET_PERCENT,
                    "requested_max_output_tokens": requested_max_output_tokens,
                    "prompt_tokens": telem["prompt_tokens"],
                    "completion_tokens": telem["completion_tokens"],
                    "reasoning_tokens": telem["reasoning_tokens"],
                    "ttft_seconds": telem["ttft_seconds"],
                    "prefill_throughput": telem["prefill_throughput_tokens_per_second"],
                    "decode_throughput": telem["decode_throughput_tokens_per_second"],
                })
                continue

            extracted: Optional[str] = None
            extraction_ok = False
            validator_results: list[Any] = []

            if suite == "python":
                ext = extract_python_module(answer)
                if ext.success:
                    extraction_ok = True
                    extracted = ext.extracted_text
                    validator_results = _py.validate(ext.extracted_text)
                else:
                    extraction_failures.append({"request_id": req_id, "reason": ext.failure_reason})
            elif suite == "java":
                ext = extract_java_solution(answer)
                if ext.success:
                    extraction_ok = True
                    extracted = ext.extracted_text
                    # Resolve the CANONICAL java case (CaseDef with .checks) by stable id.
                    # The frozen _run_case_test needs a canonical case object, never the
                    # JavaPromptSpec prompt spec -- JAVA_SPECS is only used to build prompts.
                    case = next(c for c in _java.JAVA_CASES if c.id == req_id)
                    validator_results = _java_run_case_test(case, ext.extracted_text)
                else:
                    # Only this case's canonical checks are lost; denominator stays 52.
                    extraction_failures.append({"request_id": req_id, "reason": ext.failure_reason})
            elif suite == "markdown":
                ext = extract_markdown_document(answer)
                if ext.success:
                    extraction_ok = True
                    extracted = ext.extracted_text
                    validator_results = _md.validate(ext.extracted_text)
                else:
                    extraction_failures.append({"request_id": req_id, "reason": ext.failure_reason})
            elif suite == "evidence":
                ext = extract_evidence_response(answer)
                if ext.success and answer is not None:
                    extraction_ok = True
                    extracted = answer
                    block = _evidence_block(req_id, answer)
                    validator_results = _ev.validate(block)
                else:
                    # Only this scenario's canonical checks are lost; denominator stays 30.
                    reason = ext.failure_reason if hasattr(ext, "failure_reason") else "no response text"
                    extraction_failures.append({"request_id": req_id, "reason": reason})
            elif suite == "drift":
                ext = extract_drift_response(answer)
                if ext.success and answer is not None:
                    extraction_ok = True
                    extracted = answer
                    validator_results = _drift.validate(answer)
                else:
                    reason = ext.failure_reason if hasattr(ext, "failure_reason") else "no response text"
                    extraction_failures.append({"request_id": req_id, "reason": reason})

            all_results.extend(validator_results)
            for r in validator_results:
                if not getattr(r, "passed", False):
                    validation_failures.append({
                        "case_id": getattr(r, "case_id", req_id),
                        "name": getattr(r, "name", ""),
                        "failure_type": getattr(r, "failure_type", ""),
                        "failure_reason": getattr(r, "failure_reason", ""),
                    })

            req_records.append({
                "request_id": req_id,
                "extraction_success": extraction_ok,
                "checks_passed": sum(getattr(r, "checks_passed", 0) for r in validator_results),
                "checks_total_validator": sum(getattr(r, "checks_total", 0) for r in validator_results),
                "prompt_tokens": telem["prompt_tokens"],
                "completion_tokens": telem["completion_tokens"],
                "reasoning_tokens": telem["reasoning_tokens"],
                "ttft_seconds": telem["ttft_seconds"],
                "prefill_throughput": telem["prefill_throughput_tokens_per_second"],
                "decode_throughput": telem["decode_throughput_tokens_per_second"],
                "effective_context_capacity": effective_capacity,
                "output_budget_policy": OUTPUT_BUDGET_POLICY,
                "output_budget_percent": OUTPUT_BUDGET_PERCENT,
                "requested_max_output_tokens": requested_max_output_tokens,
            })

    checks_passed, checks_total = account_checks(suite, all_results)

    ttfts = [rr["ttft_seconds"] for rr in req_records if rr["ttft_seconds"] is not None]
    prefills = [rr["prefill_throughput"] for rr in req_records if rr["prefill_throughput"] is not None]
    decodes = [rr["decode_throughput"] for rr in req_records if rr["decode_throughput"] is not None]

    return SuiteResult(
        run_id=run_id,
        suite=suite,
        model_identifier=model,
        configuration_fingerprint=fingerprint,
        classification=classification,
        reasoning_policy=REASONING_POLICY,
        loaded_reasoning_mode=loaded_reasoning_mode,
        requests_expected=len(requests),
        requests_completed=len(req_records),
        checks_passed=checks_passed,
        checks_total=checks_total,
        extraction_failures=extraction_failures,
        validation_failures=validation_failures,
        prompt_tokens=sum(rr["prompt_tokens"] for rr in req_records),
        completion_tokens=sum(rr["completion_tokens"] for rr in req_records),
        reasoning_tokens=sum(rr["reasoning_tokens"] for rr in req_records),
        wall_time_seconds=round(time.perf_counter() - t0, 4),
        ttft_seconds=(sum(ttfts) / len(ttfts)) if ttfts else None,
        prefill_throughput=_aggregate_throughput(prefills),
        decode_throughput=_aggregate_throughput(decodes),
        lm_studio_url=lm_studio_url,
        model_max_context=cfg.get("model_max_context"),
        loaded_context=cfg.get("loaded_context"),
        mtp_state=mtp_state,
        max_output_tokens=suite_requested_budget or 0,
        temperature=temperature,
        # --- Fixed output-ceiling budget telemetry (benchmark config, reproducible) ---
        effective_context_capacity=effective_capacity,
        output_budget_policy=OUTPUT_BUDGET_POLICY,
        output_budget_percent=OUTPUT_BUDGET_PERCENT,
        requested_max_output_tokens=suite_requested_budget,
        requests=req_records,
    )


# ---------------------------------------------------------------------------
# Coordinator: verify identity + combine fixed denominators.
# ---------------------------------------------------------------------------

def combine_suite_results(results: list[SuiteResult]) -> dict[str, Any]:
    """Aggregate five :class:`SuiteResult`s into the canonical ``/166`` total.

    Requires (and verifies *before* combining):

      * exactly the five expected suites present;
      * a single shared non-empty ``run_id``;
      * a single shared ``model_identifier``;
      * a single shared, non-empty ``configuration_fingerprint``;
      * each suite's reported ``checks_total`` equal to its immutable canonical
        denominator (guards against any denominator drift).

    On any failure it raises :class:`ConfigurationMismatchError` and does **not**
    aggregate. On success the final total is always python/58, java/52,
    markdown/20, evidence/30, drift/6 -> 166.
    """
    errors: list[str] = []

    if len(results) != len(SUITES):
        errors.append(f"expected {len(SUITES)} suite results, got {len(results)}")
    present = {r.suite for r in results}
    missing = [s for s in SUITES if s not in present]
    extra = [s for s in present if s not in SUITES]
    if missing:
        errors.append(f"missing suite(s): {', '.join(missing)}")
    if extra:
        errors.append(f"unexpected suite(s): {', '.join(extra)}")

    run_ids = {r.run_id for r in results}
    if len(run_ids) != 1:
        errors.append(f"run_id not shared across suites: {sorted(run_ids)}")
    elif "" in run_ids:
        errors.append("a suite reported an empty run_id")

    model_ids = {r.model_identifier for r in results}
    if len(model_ids) != 1:
        errors.append(f"model_identifier mismatch across suites: {sorted(model_ids)}")

    fingerprints = {r.configuration_fingerprint for r in results}
    if "" in fingerprints:
        errors.append("a suite reported an empty configuration_fingerprint")
    if len(fingerprints) != 1:
        errors.append(f"configuration_fingerprint mismatch across suites: {sorted(fingerprints)}")

    per_suite: dict[str, tuple[int, int]] = {}
    for r in results:
        expected_denom = CANONICAL_DENOMINATORS.get(r.suite)
        if expected_denom is not None and r.checks_total != expected_denom:
            errors.append(
                f"{r.suite}: checks_total {r.checks_total} != canonical denominator {expected_denom}"
            )
        per_suite[r.suite] = (r.checks_passed, r.checks_total)

    if errors:
        raise ConfigurationMismatchError("; ".join(errors))

    # --- Successful path: always python/58, java/52, markdown/20, evidence/30, drift/6. ---
    detail = {}
    total_passed = 0
    for suite in SUITES:
        passed, total = per_suite[suite]
        detail[suite] = {"passed": passed, "total": total}
        total_passed += passed

    aggregate = {
        "run_id": next(iter(run_ids)) if run_ids else None,
        "model_identifier": next(iter(model_ids)) if model_ids else None,
        "configuration_fingerprint": next(iter(fingerprints)) if fingerprints else "",
        "classification": results[0].classification if results else "incomplete",
        "reasoning_policy": REASONING_POLICY,
        "per_suite": detail,
        "python": f"{detail['python']['passed']}/{detail['python']['total']}",
        "java": f"{detail['java']['passed']}/{detail['java']['total']}",
        "markdown": f"{detail['markdown']['passed']}/{detail['markdown']['total']}",
        "evidence": f"{detail['evidence']['passed']}/{detail['evidence']['total']}",
        "drift": f"{detail['drift']['passed']}/{detail['drift']['total']}",
        "checks_passed": total_passed,
        "checks_total": TOTAL_DENOMINATOR,
    }
    return aggregate


# ---------------------------------------------------------------------------
# Orchestrated full run (five fresh subprocesses -> combine).
# ---------------------------------------------------------------------------

async def run_v2_run(
    lm_studio_url: str = DEFAULT_LM_STUDIO_URL,
    model: str = DEFAULT_MODEL,
    *,
    run_id: Optional[str] = None,
    project_root: Optional[str] = None,
    out_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Launch the five suites as separate OS processes, then coordinate + combine.

    Each suite is its own process (fresh state; no Pi Web session history used as
    benchmark context). Temporary JSON files are used for coordinator handoff and
    removed afterwards -- nothing is persisted to SQLite / CSV / UI.
    """
    run_id = run_id or "run-" + os.urandom(6).hex()

    # Read-only pre-check: confirm the expected model is loaded before any suite runs.
    registry = await verify_model_loaded(lm_studio_url, model)
    if not registry.get("loaded"):
        raise ConfigurationMismatchError(
            f"expected model {model!r} is not loaded in LM Studio at {lm_studio_url}"
        )

    # --- Run-level execution provenance (Act: reproducibility). Captured ONCE at
    # benchmark start from a single read-only config probe plus a host snapshot, then
    # attached to the run document at RUN level and inherited by every suite via
    # ``run_id``. Hardware/runtime/model/inference fields are NOT duplicated into the
    # per-suite records -- child evidence inherits provenance through the run id.
    model_config = await _probe_model_config(lm_studio_url, model)
    provenance = _capture_provenance(
        lm_studio_url=lm_studio_url,
        model_identifier=model,
        model_config=model_config,
    )

    root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent
    workdir = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="benchllm_v2_suite_"))

    env = dict(os.environ)
    env.update({
        "BENCH_RUN_ID": run_id,
        "BENCH_V2_MODEL": model,
        "BENCH_LM_STUDIO_URL": lm_studio_url,
    })

    results: list[SuiteResult] = []
    try:
        for suite in SUITES:
            out_path = workdir / f"{suite}.json"
            child_env = dict(env)
            child_env["BENCH_SUITE_OUT_PATH"] = str(out_path)
            # Fresh standalone process per suite.
            subprocess.run(
                [sys.executable, "-m", "src", "v2-suite", suite],
                cwd=str(root),
                env=child_env,
                check=True,
                capture_output=True,
                text=True,
            )
            data = json.loads(out_path.read_text(encoding="utf-8"))
            results.append(SuiteResult.from_dict(data))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    agg = combine_suite_results(results)

    # Attach the run-level provenance snapshot to the aggregate before persistence. It is
    # stored once at the run level; build_run_document surfaces it as a top-level key.
    agg["provenance"] = provenance

    # Durable result artifact (Act 2): persist the completed run so it can be
    # loaded later by ``run_id`` for the read-only UI. Best-effort and reported on
    # failure -- a successful benchmark must not be masked, but persistence loss
    # must never be silent. Never imports or calls the legacy executor.
    try:
        from src.v2_quality_artifact import persist_run as _persist_run

        _persist_run(agg, results)
    except OSError as exc:
        print(
            f"warning: failed to persist V2 run {agg.get('run_id')!r}: {exc}",
            file=sys.stderr,
        )

    return agg
