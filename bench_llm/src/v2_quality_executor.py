"""Live V2 quality executor (Act 11C-4C3).

Runs the *canonical* V2 quality benchmark against the currently loaded LM Studio
model. It is a thin orchestration layer built from three read-only building
blocks -- never reimplementing any of them:

    * :mod:`src.v2_quality_prompts`  -- canonical model-facing task specs + renderers
    * :mod:`src.v2_quality_extractors` -- raw response -> validator input (presentation only)
    * frozen ``src.quality.*`` validators -- the deterministic graders

It returns a single in-memory result. It deliberately does NOT persist, and it
does not touch ``V2QualityStore``, persistence, legacy scoring or the Results UI.

Design rules honoured here:

  * **Exactly 25 live requests** -- python 1, java 12, markdown 1, evidence 10,
    drift 1. Not batched or split differently.
  * **Explicit reasoning control** ``off|on``. Never silently fall back to the
    model default. If LM Studio does not honour the requested state (as reported
    by its own stats / response segments) the run is flagged incomplete/invalid
    rather than pretended canonical.
  * **Transport retry once**, and ONLY for genuine transport/server failures
    (connection error, timeout, HTTP 5xx). A wrong answer, compile failure,
    extraction/format/evidence/drift failure are never retried. The first valid
    model response is the benchmark result.
  * **No fabrication.** Extraction failures stay observable; raw responses for any
    failure remain inspectable in the returned ``requests`` list.

Development note: the HTTP transport is injectable (see ``chat_transport``) so the
plumbing can be proven offline with a stub before any real canonical run -- which
this act does NOT launch on its own.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import httpx

# --- Reuse verbatim: stimulus + adapter + frozen validators (no mutation). ---
from src.v2_quality_prompts import (
    PYTHON_SPECS, JAVA_SPECS, MARKDOWN_REPAIR_INSTRUCTION,
    evidence_scenarios, drift_rendered_scenario,
)
from src.v2_quality_extractors import (
    extract_python_module, extract_java_solution, extract_markdown_document,
    extract_evidence_response, extract_drift_response, extraction_failure_result,
)
from src.quality import python_cases as _py
from src.quality import java_cases as _java
from src.quality import markdown_cases as _md
from src.quality import evidence_cases as _ev
from src.quality import drift_cases as _drift
from src.quality.java_cases import _run_case_test as _java_run_case_test

# --- Reuse read-only: Act 11B configuration-identity helpers (no I/O at import). ---
from src.benchmark import (
    resolve_persisted_quantization, resolve_context_capacity,
    resolve_loaded_instance_config,
)
from src.results import compute_configuration_fingerprint, classify_run_for_result

# Chat endpoint path on the LM Studio base URL.
CHAT_ENDPOINT = "/api/v1/chat"


# ---------------------------------------------------------------------------
# Reasoning-control constants
# ---------------------------------------------------------------------------

VALID_REASONING = ("off", "on")
# Canonical benchmark rule: reasoning OFF for quality requests (matches
# BENCHMARK_REASONING_MODE / src/benchmark.py). Sent explicitly -- never inherited.
DEFAULT_REASONING = "off"


# ---------------------------------------------------------------------------
# Prompt builders (model-facing text only; returned raw + inspectable)
# ---------------------------------------------------------------------------

def _python_prompt() -> str:
    """One combined request for all 10 Python functions in a single module."""
    lines = [
        "Implement ALL of the following functions in a single Python module and return "
        "only that complete, runnable source code. Define each function with the exact "
        "name given below.",
        "",
    ]
    for s in PYTHON_SPECS:
        lines.append(f"[{s.id}] {s.function_name}({s.signature})")
        lines.append(f"  Behaviour: {s.behaviour}")
        lines.append(f"  Edge cases: {s.edge_cases}")
        lines.append(f"  Output requirement: {s.output_requirement}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _java_prompt(spec) -> str:
    """One independent request per Java case."""
    return (
        "Implement a compilable Java source file containing a single public class Solution "
        "with exactly the method specified below. Return only the compilable Java source.\n\n"
        f"[{spec.id}] {spec.method_signature}\n"
        f"  Behaviour: {spec.behaviour}\n"
        f"  Edge cases: {spec.edge_cases}\n"
        "  Output restriction: return only compilable source defining class Solution with "
        "this method."
    )


def _markdown_prompt() -> tuple[str, str]:
    """Return (prompt_text, input_document)."""
    instruction = MARKDOWN_REPAIR_INSTRUCTION.strip() + "\n\nInput document to repair:\n\n"
    return instruction, _md.load_broken_fixture()


def _evidence_prompt(cid: str) -> str:
    """Scenario text (verbatim from the frozen suite) plus a short instruction."""
    scenario = evidence_scenarios().get(cid, "")
    return (
        "Answer using only the information provided below. Do not fabricate or infer "
        "beyond it.\n\n" + scenario
    )


def _evidence_block(cid: str, answer: str) -> str:
    """Assemble a single gradeable EVID block -- identical layout to the frozen
    ``render_input`` per-case structure (header / scenario / RESPONSE: marker), so
    only this case's response is graded and other cases are not contaminated."""
    scenario = _ev.EVID_FIXTURES.get(cid, {}).get("scenario", "")
    return f"=== {cid} ===\n{scenario}\n\nRESPONSE:\n{answer}\n"


def _drift_prompt() -> str:
    """DRIFT-01 instruction block (verbatim render_scenario())."""
    return drift_rendered_scenario()


# ---------------------------------------------------------------------------
# Raw response -> answer text. Only the *message* segments form the semantic
# deliverable; reasoning/thinking segments are presentation wrapping and are not
# part of the code/markdown/text being graded.
# ---------------------------------------------------------------------------

def _response_text(body: Any) -> tuple[Optional[str], Optional[bool]]:
    """Return (answer_text, reasoning_observed).

    ``reasoning_observed`` is True/False when the response exposes that fact -- via a
    'reasoning' segment or an explicit ``stats.reasoning_output_tokens`` field -- and
    None when it cannot be determined from this response. The answer text is built
    from *message* segments only (reasoning/thinking is presentation wrapping, not
    the code/markdown/text being graded).
    """
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
            text = seg.get("text", "") or ""
            if seg_type == "reasoning":
                reasoning_present = True
            elif seg_type == "message" and text:
                message_parts.append(text)
    # A stat-based reading is authoritative when present; a reasoning segment wins
    # over a zeroed stat (never claim absence when the model clearly produced one).
    if reasoning_present is not True:
        rp_stats = _stats_reasoning()
        if rp_stats is not None:
            reasoning_present = rp_stats
    # Fallback: no typed 'message' segments -> take any segment carrying text.
    if not message_parts and isinstance(segments, list):
        message_parts = [seg.get("text", "") or "" for seg in segments
                         if isinstance(seg, dict) and seg.get("text")]
    answer = "\n".join(message_parts).strip() if message_parts else None
    return answer, reasoning_present


# ---------------------------------------------------------------------------
# Telemetry from an LM Studio response body.
# ---------------------------------------------------------------------------

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
# ---------------------------------------------------------------------------

async def _httpx_chat(client: httpx.AsyncClient, url: str, payload: dict) -> Any:
    """POST to LM Studio /api/v1/chat (stream=False) and return the JSON body."""
    resp = await client.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


def _is_retryable(exc: BaseException) -> bool:
    """True for genuine transport failures OR server-side HTTP 5xx responses."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            return exc.response.status_code >= 500
        except Exception:  # pragma: no cover - defensive
            return False
    return False


async def _invoke(transport: Callable, client: httpx.AsyncClient, url: str, payload: dict):
    """Invoke the transport with at most ONE retry on genuine transport/server errors.

    Returns (body, elapsed_seconds). Re-raises on unrecoverable errors or after the
    single allowed retry -- extraction/format/answer problems are never retried here.
    """
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


# ---------------------------------------------------------------------------
# Best-effort model configuration resolution (Act 11B helpers, read-only).
# Wrapped so tests can stub it offline without touching the network.
# ---------------------------------------------------------------------------

async def _resolve_model_config(lm_studio_url: str, model: str) -> dict[str, Any]:
    """Resolve machine-readable model identity + configuration for ``model``.

    Combines quantization (``resolve_persisted_quantization``), context capacity
    (``resolve_context_capacity``) and the loaded-instance inference config
    (``resolve_loaded_instance_config``). Best-effort: every helper records a real
    value or an honest failure-mode label and never fabricates. ``loaded_context``
    prefers the loaded instance's configured context, falling back to the resolved
    maximum when unavailable.
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
        "reasoning_mode": instance.get("reasoning_mode", DEFAULT_REASONING),
        "flash_attention": instance.get("flash_attention"),
        "offload_kv_cache_to_gpu": instance.get("offload_kv_cache_to_gpu"),
        "eval_batch_size": instance.get("eval_batch_size"),
        "physical_batch_size": instance.get("physical_batch_size"),
        "parallel": instance.get("parallel"),
        "kv_cache_k_quantization": instance.get("kv_cache_k_quantization"),
        "kv_cache_v_quantization": instance.get("kv_cache_v_quantization"),
    }


# ---------------------------------------------------------------------------
# Result synthesis when extraction cannot identify the model's output.
# Records an observable ``extraction_failure`` so Results can surface it; never
# fabricates a pass and contributes no check counts (checks_total = 0).
# ---------------------------------------------------------------------------

def _failure_results(case_ids: list[str], reason: str) -> list[Any]:
    return [extraction_failure_result(cid, "v2", cid, reason) for cid in case_ids]


# ---------------------------------------------------------------------------
# Aggregation helpers (counts derived from real CaseResult objects; never hardcoded).
# ---------------------------------------------------------------------------

def _aggregate(results: list[Any]) -> tuple[int, int, int]:
    """Return (result_entries, checks_passed, checks_total) from CaseResult-like objects."""
    result_entries = len(results)
    checks_passed = sum(r.checks_passed for r in results if getattr(r, "checks_passed", 0) is not None)
    checks_total = sum(r.checks_total for r in results if getattr(r, "checks_total", 0) is not None)
    return result_entries, checks_passed, checks_total


# ---------------------------------------------------------------------------
# Request scheduling -- the canonical 25-request topology.
# Each entry describes how to build the payload and process the response.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Request:
    suite: str
    request_id: str
    prompt: str


def _build_requests() -> list[_Request]:
    """The exact canonical 25-request set (python 1, java 12, markdown 1,
    evidence 10, drift 1)."""
    requests: list[_Request] = [_Request("python", "PY", _python_prompt())]
    requests += [_Request("java", c.id, _java_prompt(c)) for c in JAVA_SPECS]
    md_prompt, _ = _markdown_prompt()
    requests.append(_Request("markdown", "MD", md_prompt))
    requests += [_Request("evidence", cid, _evidence_prompt(cid)) for cid in evidence_scenarios()]
    requests.append(_Request("drift", "DRIFT-01", _drift_prompt()))
    return requests


# ---------------------------------------------------------------------------
# Live executor.
# ---------------------------------------------------------------------------

async def run_v2_quality_live(
    lm_studio_url: str,
    model: str,
    *,
    reasoning: str = DEFAULT_REASONING,
    temperature: float = 0.0,
    max_output_tokens: int = 4096,
    chat_transport: Optional[Callable[[httpx.AsyncClient, str, dict], Awaitable[Any]]] = None,
) -> dict[str, Any]:
    """Run the canonical V2 quality benchmark against the loaded LM Studio model.

    Args:
        lm_studio_url: LM Studio server base URL (e.g. ``http://localhost:1234``).
        model: Loaded model key/identifier to run.
        reasoning: Explicit inference-configuration control -- "off" or "on". Sent
            in every request payload; never silently changed to the model default.
        temperature: Sampling temperature (0.0 for deterministic grading).
        max_output_tokens: Upper bound on output tokens per request.
        chat_transport: Optional injected async transport ``(client, url, payload) -> body``.
            Defaults to a real httpx POST; tests inject a stub so plumbing can be
            proven offline. When omitted the live LM Studio API is used.

    Returns an in-memory dict with model/config identity, reasoning honour status,
    configuration fingerprint + classification, per-request raw telemetry (including
    failures), and aggregated validator results. No persistence occurs.
    """
    if reasoning not in VALID_REASONING:
        raise ValueError(f"reasoning must be one of {VALID_REASONING}, got {reasoning!r}")

    base_url = lm_studio_url.rstrip("/")
    chat_url = f"{base_url}{CHAT_ENDPOINT}"
    transport = chat_transport or _httpx_chat

    # --- Model identity + configuration (best-effort, read-only). ---
    cfg = await _resolve_model_config(lm_studio_url, model)

    requests = _build_requests()

    all_results: list[Any] = []
    records: list[dict[str, Any]] = []
    observed_reasoning: list[Optional[bool]] = []

    async with httpx.AsyncClient(timeout=300.0) as client:
        for req in requests:
            payload = {
                "model": model,
                "input": req.prompt,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "stream": False,
                "store": False,
                "reasoning": reasoning,
            }

            body, elapsed = await _invoke(transport, client, chat_url, payload)
            answer, reasoning_present = _response_text(body)
            observed_reasoning.append(reasoning_present)
            telem = _telemetry(body, elapsed)

            extracted: Optional[str] = None
            extraction_ok = False
            failure_type = ""
            failure_reason = ""
            validator_results: list[Any] = []

            # --- Suite-specific extract + validate wiring. ---
            if req.suite == "python":
                ext = extract_python_module(answer)
                if ext.success:
                    extracted, extraction_ok = ext.extracted_text, True
                    validator_results = _py.validate(ext.extracted_text)  # flat per-Check across PY-01..PY-10
                else:
                    failure_type, failure_reason = ext.failure_type, ext.failure_reason
                    validator_results = _failure_results([c.id for c in _py.PY_CASES], ext.failure_reason)

            elif req.suite == "java":
                ext = extract_java_solution(answer)
                if ext.success:
                    extracted, extraction_ok = ext.extracted_text, True
                    # Per-case harness (Java solutions collide when concatenated).
                    case = next(c for c in _java.JAVA_CASES if c.id == req.request_id)
                    validator_results = _java_run_case_test(case, ext.extracted_text)
                else:
                    failure_type, failure_reason = ext.failure_type, ext.failure_reason
                    validator_results = [extraction_failure_result(req.request_id, "java", req.request_id, ext.failure_reason)]

            elif req.suite == "markdown":
                ext = extract_markdown_document(answer)
                if ext.success:
                    extracted, extraction_ok = ext.extracted_text, True
                    validator_results = _md.validate(ext.extracted_text)  # one per MD defect
                else:
                    failure_type, failure_reason = ext.failure_type, ext.failure_reason
                    validator_results = _failure_results([c.id for c in _md.MARKDOWN_CASES], ext.failure_reason)

            elif req.suite == "evidence":
                ext = extract_evidence_response(answer)  # identity passthrough
                if not ext.success:
                    failure_type, failure_reason = ext.failure_type, ext.failure_reason
                    validator_results = [extraction_failure_result(req.request_id, "evidence", req.request_id, ext.failure_reason)]
                else:
                    extraction_ok, extracted = True, ext.extracted_text
                    block = _evidence_block(req.request_id, ext.extracted_text)
                    validator_results = _ev.validate(block)  # one per case

            elif req.suite == "drift":
                ext = extract_drift_response(answer)  # identity passthrough (no JSON normalisation)
                if not ext.success:
                    failure_type, failure_reason = ext.failure_type, ext.failure_reason
                    validator_results = [extraction_failure_result(req.request_id, "drift", req.request_id, ext.failure_reason)]
                else:
                    extraction_ok, extracted = True, ext.extracted_text
                    validator_results = _drift.validate(ext.extracted_text)

            all_results.extend(validator_results)

            records.append({
                "suite": req.suite,
                "request_id": req.request_id,
                "raw_prompt": req.prompt,
                "raw_response": answer if answer is not None else "",
                "extracted_source": extracted if extracted is not None else "",
                "extraction_success": extraction_ok,
                "extraction_failure_type": failure_type or "",
                "extraction_failure_reason": failure_reason or "",
                "checks_passed": sum(r.checks_passed for r in validator_results),
                "checks_total": sum(r.checks_total for r in validator_results),
                "duration_seconds": round(elapsed, 4),
                "prompt_tokens": telem["prompt_tokens"],
                "completion_tokens": telem["completion_tokens"],
                "reasoning_tokens": telem["reasoning_tokens"],
                "ttft_seconds": telem["ttft_seconds"],
                "prefill_throughput_tokens_per_second": (
                    round(telem["prefill_throughput_tokens_per_second"], 3)
                    if telem["prefill_throughput_tokens_per_second"] is not None else None
                ),
                "decode_throughput_tokens_per_second": (
                    round(telem["decode_throughput_tokens_per_second"], 3)
                    if telem["decode_throughput_tokens_per_second"] is not None else None
                ),
                "reasoning_observed_present": reasoning_present,
                "case_results": [r.to_dict() for r in validator_results],
            })

    # --- Aggregate (derived from real CaseResult objects). ---
    result_entries, checks_passed, checks_total = _aggregate(all_results)
    logical_cases_attempted = (
        len(_py.PY_CASES) + len(_java.JAVA_CASES) + len(_md.MARKDOWN_CASES)
        + len(list(evidence_scenarios())) + len(_drift.DRIFT_CASES)
    )

    # --- Reasoning honour + classification (Act 11B, read-only). ---
    def _honoured(observed: Optional[bool]) -> bool:
        # Requested "off" is honoured iff no reasoning was produced; requested "on" is
        # honoured iff reasoning WAS produced. "Not reported" cannot assert a violation.
        want = reasoning == "on"
        return (want == observed) if observed is not None else True

    reasoning_honoured = all(_honoured(o) for o in observed_reasoning) and any(
        o is not None for o in observed_reasoning
    )

    fingerprint_run = dict(cfg)
    fingerprint_run["reasoning_mode"] = reasoning  # what was actually requested/sent
    configuration_fingerprint = compute_configuration_fingerprint(fingerprint_run)
    classification = classify_run_for_result(fingerprint_run)

    if not reasoning_honoured:
        validity = "invalid_reasoning"
    elif classification == "canonical" and bool(configuration_fingerprint):
        validity = "canonical"
    else:
        validity = "incomplete"

    return {
        "suite": "v2-quality",
        "model_identifier": model,
        "configuration_fingerprint": configuration_fingerprint,
        "classification": classification,
        "validity": validity,
        # Model/config identity (machine-readable; never fabricated).
        "model_quantization": cfg.get("model_quantization"),
        "loaded_context": cfg.get("loaded_context"),
        "model_max_context": cfg.get("model_max_context"),
        "reasoning_requested": reasoning,
        "reasoning_honoured": reasoning_honoured,
        # Aggregate (checks_passed / total derived; logical_cases/result_entries structural).
        "logical_cases_attempted": logical_cases_attempted,
        "result_entries": result_entries,
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        # Aliases mirroring the existing V2 naming convention.
        "v2_quality_checks_passed": checks_passed,
        "v2_quality_checks_total": checks_total,
        # Per-request raw telemetry (failures remain inspectable).
        "requests": records,
    }
