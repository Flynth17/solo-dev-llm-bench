"""Context benchmark family runner (RM-26-AA-0009).

Deterministic, evidence-preserving measurement of **correctness/retention as
usable context grows**. This module owns the *benchmark family only* -- it does
NOT implement the Context Results UI (that is RM-26-AA-0018), nor composite
scoring, nor anything overlapping Standard Speed (throughput) or Workflow
(multi-suite correctness).

============================================================================
LOCKED CONTRACT  (RM-26-AA-0009)
============================================================================

Question answered
    "What happens to a model's correctness on the *same* deterministic task as
     its effective context grows?"

Held constant across context points
    * model / version and quantization / configuration (keyed by fingerprint)
    * the gradeable fact set -- 12 fixed keyed facts with fixed expected values
    * what is requested at every point -- all 12 keys, always
    * evaluation method -- exact string match against known values (no judge)
    * temperature = 0.0, reasoning off (deterministic generation)
    * prompt construction rules -- filler scales; facts block + instruction fixed

What varies
    * only the volume of deterministic *filler* before the facts block, sized to
      each requested context point. Facts therefore sink deeper as context grows,
      producing a position-sensitive retention signal.

Context points (requested tokens)
    15K / 30K / 60K / 120K / 180K / 240K -- the roadmap's values "where supported".
    These are *requested* targets; actual input_tokens are recorded per point.

Baseline
    The smallest *supported* point (ascending order). Degradation is a plain
    delta from that baseline; retention is score/baseline.

Raw metric per point
    ``facts_correct / facts_requested`` in [0, 1], or ``None`` when nothing was
    gradeable -- never coerced to zero.

Degradation calculation
    ``degradation = score(point) - score(baseline)``;
    ``retention = score(point) / score(baseline)`` (None when baseline is None/0).
    No composite / overall score is ever computed.

Repetition
    One deterministic run per point at temperature 0. No cold/warm contract --
    repeated runs are identical by construction and would add cost without signal;
    independent *runs* (distinct ids) are still persisted separately.

Unsupported behaviour
    A point above the model's effective/loaded capacity is persisted as
    ``unsupported`` with no score, actual tokens recorded, never zeroed and never
    rescaled. Later supported points are unaffected.

Failure-state model (never collapsed into degradation)
    success / extraction_failure (no usable answer) / malformed / failed
    (operational: HTTP/timeout/persistence/load-not-loaded). Operational failures
    are distinct from capability degradation.

Persistence
    One atomic JSON artifact per run under ``data/context_runs/<run_id>.json``
    (see :mod:`src.v2_context_artifact`), mirroring the Workflow v2-artifact
    convention -- not the Speed-oriented wide results table. Every point carries
    model + configuration fingerprint so ownership can be verified.

Read model
    :mod:`src.v2_context_read_model` projects the artifact into per-point scores,
    baseline, degradation curve, gaps and validity for the future UI without any
    frontend recomputation.
============================================================================
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

import httpx

# --- Reuse read-only: canonical config-identity helpers (no I/O at import). ---
from src.benchmark import (
    resolve_persisted_quantization,
    resolve_context_capacity,
    resolve_loaded_instance_config,
)
from src.results import compute_configuration_fingerprint, classify_run_for_result
# Run-level execution provenance (Act: reproducibility). Stdlib-only capture module.
from src.provenance import capture_provenance as _capture_provenance

# --- Reuse read-only: the shared read-only model-loaded probe. ---
from src.v2_quality_suite_runner import verify_model_loaded

# --- Locked deterministic corpus + scoring (pure; no mutation). ---
from src.context_corpus import (
    CONTEXT_POINTS,
    CONTEXT_POINT_LABELS,
    NUM_FACTS,
    build_prompt,
    facts,
    requested_fact_keys,
)
from src.context_corpus import scoring as _scoring
from src.context_corpus.scoring import (
    STATUS_EXTRACTION_FAILURE,
    STATUS_FAILED,
    STATUS_MALFORMED,
    STATUS_SUCCESS,
    STATUS_UNSUPPORTED,
)

# --- Persistence-only layer (serializes already-computed documents). ---
import src.v2_context_artifact as _artifact

CHAT_ENDPOINT = "/api/v1/chat"

DEFAULT_LM_STUDIO_URL = os.environ.get("BENCH_LM_STUDIO_URL", "http://127.0.0.1:1234")
DEFAULT_MODEL = os.environ.get("BENCH_V2_MODEL", "ornith-1.5-35b-a3b")

# Deterministic generation for the Context correctness task.
CONTEXT_TEMPERATURE: float = 0.0
# Enough to list NUM_FACTS short values; output is tiny relative to prompt.
CONTEXT_MAX_OUTPUT_TOKENS: int = 768
# Benchmark-contract schema version (distinct from the on-disk artifact version).
BENCHMARK_SCHEMA_VERSION: int = 1

# Reference filler word count for the single online calibration probe used to size
# prompts when no token counter is injected. Best-effort; actual input_tokens are
# recorded authoritatively per point regardless.
_CALIBRATION_WORDS: int = 256

TokenCount = Callable[[str], int]


class ContextRunError(Exception):
    """Pre-run validation / concurrency failure (mapped to 4xx by the route).

    Carries a concise, human-safe message -- never a stack trace or internal detail.
    """


# ---------------------------------------------------------------------------
# Answer extraction: turn model text into per-key values for the requested keys.
# ---------------------------------------------------------------------------

def _extract_answers(text: Optional[str], keys: Sequence[str]) -> dict[str, Optional[str]]:
    """Parse ``KEY: value`` lines from *text* for each requested *key*.

    Returns a mapping of every requested key to its normalized returned value
    (``None`` when the key was absent or blank). Case-sensitive on the key prefix
    so ``F01`` never matches ``f01``; only the first occurrence per key is used.
    """
    answers: dict[str, Optional[str]] = {key: None for key in keys}
    if not text:
        return answers
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        if key in answers:
            answers[key] = value.strip() or None
    return answers


# ---------------------------------------------------------------------------
# Token sizing.
# ---------------------------------------------------------------------------

def _word_count(text: str) -> int:
    return len(text.split())


class BackendTokenCounter:
    """Best-effort token counter calibrated with one cheap probe (online path).

    Performs a single reference request to learn this model/session's
    words-per-token ratio, then estimates any prompt's token count from its word
    count. Construction sizing only -- the runner records the *actual*
    ``input_tokens`` reported by the backend per point, which is authoritative.
    """

    def __init__(
        self,
        transport: Callable[[httpx.AsyncClient, str, dict], Awaitable[Any]],
        client: httpx.AsyncClient,
        url: str,
        model: str,
    ) -> None:
        self._ratio = 1.0
        self._calibrated = False
        self._warmup(model)
        self._transport = transport
        self._client = client
        self._url = url
        self._model = model

    def _warmup(self, model: str) -> None:
        # Cheap deterministic fallback if we cannot calibrate online.
        self._ratio = 0.75  # rough avg tokens/word; overridden by probe when possible

    async def calibrate(self) -> float:
        """Send one reference probe and return the words-per-token ratio."""
        ref_prompt = "context-calibration " + " ".join(
            f"w{i}" for i in range(_CALIBRATION_WORDS)
        )
        payload = {
            "model": self._model,
            "input": ref_prompt,
            "temperature": 0.0,
            "max_output_tokens": 1,
            "stream": False,
            "store": False,
            "reasoning": "off",
        }
        try:
            body = await self._transport(self._client, self._url, payload)
            stats = body.get("stats") if isinstance(body, dict) else {}
            stats = stats if isinstance(stats, dict) else {}
            input_tokens = stats.get("input_tokens")
            if isinstance(input_tokens, int) and not isinstance(input_tokens, bool) and input_tokens > _CALIBRATION_WORDS:
                # Subtract the reference instruction overhead (~ fixed).
                overhead = 8
                denom = max(1, _CALIBRATION_WORDS - overhead)
                self._ratio = (input_tokens - overhead) / denom
        except Exception:
            # Keep the deterministic fallback ratio; actuals are authoritative anyway.
            pass
        self._calibrated = True
        return self._ratio

    def __call__(self, text: str) -> int:
        return max(1, int(round(_word_count(text) * self._ratio)))


# ---------------------------------------------------------------------------
# Payload construction (reasoning off for deterministic correctness).
# ---------------------------------------------------------------------------

def _build_chat_payload(model: str, prompt: str) -> dict[str, Any]:
    return {
        "model": model,
        "input": prompt,
        "temperature": CONTEXT_TEMPERATURE,
        "max_output_tokens": CONTEXT_MAX_OUTPUT_TOKENS,
        "stream": False,
        "store": False,
        "reasoning": "off",
    }


async def _default_transport(client: httpx.AsyncClient, url: str, payload: dict) -> Any:
    resp = await client.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


def _response_text(body: Any) -> Optional[str]:
    """Return the model's final message text (``type=message`` segments), or None."""
    if not isinstance(body, dict):
        return None
    parts: list[str] = []
    segments = body.get("output")
    if isinstance(segments, list):
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = seg.get("content") or seg.get("text")
            if isinstance(text, str) and seg.get("type") == "message" and text:
                parts.append(text)
            elif isinstance(text, str) and not parts:
                # Fall back to the first non-empty string segment if no 'message' type.
                parts.append(text)
    return "\n".join(parts) if parts else None


def _telemetry_from_body(body: Any) -> dict[str, Any]:
    stats = body.get("stats") if isinstance(body, dict) else {}
    stats = stats if isinstance(stats, dict) else {}

    def _int(key: str) -> Optional[int]:
        v = stats.get(key)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    ttft = stats.get("time_to_first_token_seconds")
    try:
        ttft = float(ttft) if ttft is not None else None
    except (TypeError, ValueError):
        ttft = None
    return {
        "input_tokens": _int("input_tokens"),
        "output_tokens": _int("total_output_tokens"),
        "ttft_seconds": ttft,
    }


# ---------------------------------------------------------------------------
# Per-point execution.
# ---------------------------------------------------------------------------

async def _run_point(
    point: int,
    *,
    model: str,
    base_url: str,
    run_salt: str,
    token_count: TokenCount,
    transport: Callable[[httpx.AsyncClient, str, dict], Awaitable[Any]],
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Execute one context point and return its authoritative summary.

    Never raises for operational errors -- every failure mode is captured as a
    distinct status so operational failures are never read as degradation.
    """
    label = CONTEXT_POINT_LABELS.get(point, f"{point}")
    keys = requested_fact_keys()
    fact_values = dict(facts())

    prompt = build_prompt(
        point, filler_salt=run_salt, token_count=token_count
    )
    payload = _build_chat_payload(model, prompt)
    chat_url = f"{base_url.rstrip('/')}{CHAT_ENDPOINT}"

    try:
        body, _elapsed = await _invoke_once(transport, client, chat_url, payload)
    except Exception as exc:  # operational failure (HTTP/timeout/connection)
        return {
            "context_point": label,
            "requested_context_tokens": point,
            "actual_context_tokens": None,
            "status": STATUS_FAILED,
            "score": None,
            "facts_requested": NUM_FACTS,
            "facts_correct": 0,
            "baseline": False,
            "degradation_from_baseline": None,
            "retention_relative_to_baseline": None,
            "failure_reason": f"{type(exc).__name__}: {exc}",
            "evidence": [],
            "telemetry": {},
        }

    telemetry = _telemetry_from_body(body)
    answer = _response_text(body)

    if answer is None:
        return {
            "context_point": label,
            "requested_context_tokens": point,
            "actual_context_tokens": telemetry.get("input_tokens"),
            "status": STATUS_EXTRACTION_FAILURE,
            "score": None,
            "facts_requested": NUM_FACTS,
            "facts_correct": 0,
            "baseline": False,
            "degradation_from_baseline": None,
            "retention_relative_to_baseline": None,
            "failure_reason": "no usable answer text",
            "evidence": [
                {"key": k, "expected": fact_values[k], "actual": None, "passed": False}
                for k in keys
            ],
            "telemetry": telemetry,
        }

    answers = _extract_answers(answer, keys)
    records: list[tuple[str, str, Optional[str], bool]] = []
    for k in keys:
        passed, actual = _scoring.evaluate_fact(k, fact_values[k], answers.get(k))
        records.append((k, fact_values[k], actual, passed))

    summary = _scoring.summarize_point(records, status=STATUS_SUCCESS)
    return {
        "context_point": label,
        "requested_context_tokens": point,
        "actual_context_tokens": telemetry.get("input_tokens"),
        "status": summary["status"],
        "score": summary["score"],
        "facts_requested": summary["facts_requested"],
        "facts_correct": summary["facts_correct"],
        "baseline": False,
        "degradation_from_baseline": None,
        "retention_relative_to_baseline": None,
        "failure_reason": None,
        "evidence": summary["evidence"],
        "telemetry": telemetry,
    }


async def _invoke_once(
    transport: Callable[[httpx.AsyncClient, str, dict], Awaitable[Any]],
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
):
    start = time.perf_counter()
    body = await transport(client, url, payload)
    return body, time.perf_counter() - start


# ---------------------------------------------------------------------------
# Suite orchestration.
# ---------------------------------------------------------------------------

def generate_context_run_id() -> str:
    """Generate a server-owned run id matching the persisted-artifact naming."""
    return "ctx-" + os.urandom(6).hex()


async def run_context_suite(
    lm_studio_url: str,
    model: str,
    *,
    run_id: Optional[str] = None,
    context_points: Optional[Sequence[int]] = None,
    token_count: Optional[TokenCount] = None,
    chat_transport: Optional[Callable[[httpx.AsyncClient, str, dict], Awaitable[Any]]] = None,
    model_config_override: Optional[dict[str, Any]] = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Execute the Context benchmark across all context points and persist it.

    ``context_points`` overrides the canonical points (tests / advanced use); the
    HTTP route never passes it, so the locked contract is fixed at the API edge.
    ``token_count`` and ``chat_transport`` are injectable for fully offline testing;
    when omitted the runner calibrates a backend counter online and uses the real
    ``/api/v1/chat`` POST.

    Returns the authoritative run summary (also persisted as an artifact). Does not
    perform any benchmark-mechanic mutation beyond what the locked contract allows.
    """
    if model is None or not isinstance(model, str) or not model.strip():
        raise ContextRunError("A model must be selected to run the Context suite.")

    clean_model = model.strip()
    base_url = (lm_studio_url or "").strip().rstrip("/")
    rid = (run_id or "").strip() or generate_context_run_id()
    run_salt = uuid.uuid4().hex
    points = list(context_points) if context_points else list(CONTEXT_POINTS)

    # --- Identity + configuration (read-only), resolved once for the whole suite. ---
    if model_config_override is not None:
        cfg = dict(model_config_override)
    else:
        quantization = await resolve_persisted_quantization(base_url, clean_model)
        capacity = await resolve_context_capacity(base_url, clean_model)
        inst = await resolve_loaded_instance_config(base_url, clean_model)
        loaded_context = inst.get("loaded_context") or capacity.get("loaded_context")
        cfg = {
            "model_key": clean_model,
            "model_quantization": quantization,
            "loaded_context": loaded_context,
            "model_max_context": capacity.get("model_max_context"),
            "reasoning_mode": "off",
            "flash_attention": inst.get("flash_attention"),
            "offload_kv_cache_to_gpu": inst.get("offload_kv_cache_to_gpu"),
            "eval_batch_size": inst.get("eval_batch_size"),
            "physical_batch_size": inst.get("physical_batch_size"),
            "parallel": inst.get("parallel"),
            "num_experts": inst.get("num_experts"),
            "speculative_draft_mtp": inst.get("speculative_draft_mtp"),
            "speculative_draft_simple": inst.get("speculative_draft_simple"),
        }

    fingerprint = compute_configuration_fingerprint(cfg)
    classification = classify_run_for_result(cfg)

    # --- Run-level execution provenance (Act: reproducibility). Captured ONCE at
    # benchmark start from the resolved config + a host snapshot, then attached to the
    # run document at RUN level and inherited by every point via context_run_id. Never
    # duplicated into per-point evidence.
    provenance = _capture_provenance(
        lm_studio_url=base_url,
        model_identifier=clean_model,
        model_config=cfg,
    )

    # --- Confirm the expected model is loaded (read-only) before spending work. ---
    if model_config_override is None:
        registry = await verify_model_loaded(base_url, clean_model)
        if not registry.get("loaded"):
            raise ContextRunError(
                f"expected model {clean_model!r} is not loaded in LM Studio at {base_url}"
            )

    effective_capacity = _effective_capacity(cfg.get("model_max_context"), cfg.get("loaded_context"))

    # --- Token sizing: injected counter, else a calibrated backend probe. ---
    transport = chat_transport or _default_transport
    used_calibration = False
    if token_count is None:
        async with httpx.AsyncClient(timeout=300.0) as client:
            counter = BackendTokenCounter(transport, client, f"{base_url.rstrip('/')}{CHAT_ENDPOINT}", clean_model)
            await counter.calibrate()
            token_count = counter
            used_calibration = True
    else:
        # Still use the real transport when only the counter was injected.
        pass

    chat_url = f"{base_url.rstrip('/')}{CHAT_ENDPOINT}"

    points_summary: list[dict[str, Any]] = []
    supported_scores: list[tuple[int, Optional[float], dict]] = []  # (point, score, summary)

    async with httpx.AsyncClient(timeout=300.0) as client:
        for point in points:
            if not _capacity_supports(effective_capacity, point):
                # Below effective capacity -> unsupported gap; never zeroed/rescaled.
                summary = {
                    "context_point": CONTEXT_POINT_LABELS.get(point, f"{point}"),
                    "requested_context_tokens": point,
                    "actual_context_tokens": None,
                    "status": STATUS_UNSUPPORTED,
                    "score": None,
                    "facts_requested": NUM_FACTS,
                    "facts_correct": 0,
                    "baseline": False,
                    "degradation_from_baseline": None,
                    "retention_relative_to_baseline": None,
                    "failure_reason": (
                        f"effective capacity {effective_capacity} < requested {point}"
                        if effective_capacity is not None
                        else "context capacity unknown; cannot confirm support"
                    ),
                    "evidence": [],
                    "telemetry": {},
                }
                points_summary.append(summary)
                continue

            summary = await _run_point(
                point,
                model=clean_model,
                base_url=base_url,
                run_salt=run_salt,
                token_count=token_count,
                transport=transport,
                client=client,
            )
            points_summary.append(summary)
            if summary["status"] == _scoring.STATUS_SUCCESS and summary["score"] is not None:
                supported_scores.append((point, summary["score"], summary))

    # --- Baseline = smallest supported point; derive degradation over authoritative scores. ---
    baseline_point: Optional[int] = None
    baseline_score: Optional[float] = None
    if supported_scores:
        supported_scores.sort(key=lambda item: item[0])
        baseline_point, baseline_score, _ = supported_scores[0]

    for point, score, summary in supported_scores:
        is_baseline = point == baseline_point
        summary["baseline"] = is_baseline
        summary["degradation_from_baseline"] = _scoring.degradation_from_baseline(score, baseline_score)
        summary["retention_relative_to_baseline"] = _scoring.retention_relative_to_baseline(
            score, baseline_score
        )

    # Order points ascending by requested tokens for the curve.
    points_summary.sort(key=lambda s: s["requested_context_tokens"])

    overall_status = _overall_status(points_summary)

    document = {
        "context_run_id": rid,
        "artifact_type": _artifact.ARTIFACT_TYPE,
        "provenance": provenance,
        "generated_at": _now_iso(),
        "model_key": cfg.get("model_key"),
        "model_display_name": clean_model,
        "model_quantization": cfg.get("model_quantization"),
        "configuration_fingerprint": fingerprint,
        "classification": classification,
        "lm_studio_url": base_url,
        "temperature": CONTEXT_TEMPERATURE,
        "max_output_tokens": CONTEXT_MAX_OUTPUT_TOKENS,
        "benchmark_schema_version": BENCHMARK_SCHEMA_VERSION,
        "context_points_contract": list(CONTEXT_POINTS),
        "baseline_context_point": (
            next((s["context_point"] for s in points_summary if s["requested_context_tokens"] == baseline_point), None)
            if baseline_point is not None
            else None
        ),
        "effective_capacity": effective_capacity,
        "status": overall_status,
        "points": points_summary,
    }

    if persist:
        try:
            _artifact.persist_run(document)
        except OSError as exc:  # pragma: no cover - defensive; persistence loss must not crash
            print(
                f"warning: failed to persist Context run {rid!r}: {exc}",
                file=sys.stderr,
            )

    return document


def _overall_status(points: Sequence[dict[str, Any]]) -> str:
    statuses = [p["status"] for p in points]
    if all(s == STATUS_UNSUPPORTED for s in statuses):
        return "unsupported"
    if any(s in (_scoring.STATUS_SUCCESS,) for s in statuses):
        return "completed"
    return "failed"


def _effective_capacity(model_max_context: Optional[int], loaded_context: Optional[int]) -> Optional[int]:
    known = [c for c in (model_max_context, loaded_context)
             if isinstance(c, int) and not isinstance(c, bool) and c > 0]
    return min(known) if known else None


def _capacity_supports(effective_capacity: Optional[int], point: int) -> bool:
    """True when effective capacity can plausibly serve *point*.

    A point is supported only when we have a positive effective capacity that is at
    least the requested target. Unknown capacity (``None``) is treated as *not
    confirmed supported* so we never guess and silently run an over-long prompt --
    it surfaces as an unsupported gap instead, to be resolved by actuals.
    """
    if effective_capacity is None:
        return False
    return effective_capacity >= point


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Launch orchestration lives in the route layer (execution plumbing only); this
# module performs no benchmark logic at the HTTP edge.
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover - exercised via `python -m src context-suite`
    import argparse
    import json as _json

    def _main(argv: Optional[list[str]] = None) -> int:
        parser = argparse.ArgumentParser(prog="context-suite", description="Context benchmark suite")
        parser.add_argument("--lm-studio-url", default=DEFAULT_LM_STUDIO_URL)
        parser.add_argument("--model", default=DEFAULT_MODEL)
        parser.add_argument("--run-id", default=None)
        args = parser.parse_args(argv)

        try:
            doc = asyncio.run(run_context_suite(
                lm_studio_url=args.lm_studio_url,
                model=args.model,
                run_id=args.run_id,
            ))
        except Exception as exc:
            print(_json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
            return 1
        print(_json.dumps(doc, indent=2, default=str))
        return 0

    raise SystemExit(_main())
