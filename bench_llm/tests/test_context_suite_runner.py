"""Integration tests for the Context benchmark runner (RM-26-AA-0009).

Every test injects ``chat_transport`` + ``token_count`` + ``model_config_override``
so no LM Studio endpoint, subprocess or disk outside a throwaway tmp dir is touched.

Covers the contract-critical behaviours:

* unsupported points persist as null-scored gaps (never zero); later supported
  points are unaffected
* baseline = smallest supported point; degradation computed over authoritative
  per-point scores
* independent run identity + persistence round-trip through the read model
* ownership isolation: two models' artifacts each carry their own fingerprint --
  no cross-model evidence leakage (the class of bug Speed had)
* operational failures are captured as distinct statuses, not crashes
* generation is deterministic (temperature 0, reasoning off)
"""

from __future__ import annotations

import asyncio

import pytest

import src.v2_context_artifact as art
import src.v2_context_suite_runner as r
from src.v2_context_suite_runner import run_context_suite
from src.v2_context_read_model import load_context_result


MODEL = "test-model"
URL = "http://127.0.0.1:1234"


def asyncio_run(coro):
    """Run a coroutine to completion (matches the existing executor-test convention)."""
    return asyncio.run(coro)


def _wc(text: str) -> int:
    return max(1, len(text.split()))


def _cfg(model=MODEL, loaded_context=131072, model_max_context=131072):
    return {
        "model_key": model,
        "model_quantization": "8bit",
        "loaded_context": loaded_context,
        "model_max_context": model_max_context,
        "reasoning_mode": "off",
        "kv_cache_k_quantization": "af8",
        "kv_cache_v_quantization": "af8",
        "flash_attention": True,
        "offload_kv_cache_to_gpu": False,
        "eval_batch_size": 8,
        "physical_batch_size": 8,
        "parallel": 1,
        "num_experts": None,
        "speculative_draft_mtp": False,
        "speculative_draft_simple": False,
    }


@pytest.fixture()
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(art, "CONTEXT_RUNS_DIR", tmp_path)
    return tmp_path


def _transport_factory(correct_until_last_n=0):
    """Return a transport that answers all facts correctly except the last *n*."""
    from src.context_corpus import facts, requested_fact_keys

    kv = dict(facts())

    async def _t(client, url, payload):
        keys = requested_fact_keys()
        lines = []
        for k in keys:
            if k in keys[-correct_until_last_n:] and correct_until_last_n:
                lines.append(f"{k}: WRONG")
            else:
                lines.append(f"{k}: {kv[k]}")
        return {
            "output": [{"type": "message", "content": "\n".join(lines)}],
            "stats": {
                "input_tokens": max(1, len(payload["input"].split())),
                "total_output_tokens": 42,
                "time_to_first_token_seconds": 0.01,
            },
        }

    return _t


# ---------------------------------------------------------------------------
# Unsupported handling (never zero)
# ---------------------------------------------------------------------------

def test_unsupported_point_is_gap_not_zero(runs_dir):
    # capacity 3000 -> only the first (2000) point is supported; 8000 unsupported
    doc = asyncio_run(run_context_suite(
        URL, MODEL,
        context_points=[2000, 8000],
        token_count=_wc,
        chat_transport=_transport_factory(),
        model_config_override=_cfg(loaded_context=3000, model_max_context=3000),
        persist=True,
    ))
    unsupported = [p for p in doc["points"] if p["status"] == "unsupported"]
    assert len(unsupported) == 1
    point8k = unsupported[0]
    assert point8k["requested_context_tokens"] == 8000
    # THE critical invariant: unsupported is a null-scored gap, never numeric zero.
    assert point8k["score"] is None
    assert point8k["actual_context_tokens"] is None
    assert "capacity" in (point8k["failure_reason"] or "").lower()


# ---------------------------------------------------------------------------
# Baseline + degradation over authoritative per-point scores
# ---------------------------------------------------------------------------

def test_baseline_is_smallest_supported_and_degradation_computed(runs_dir):
    # last two facts fail at BOTH points -> 10/12 = 0.8333 at each; baseline is 2000
    doc = asyncio_run(run_context_suite(
        URL, MODEL,
        context_points=[2000, 8000],
        token_count=_wc,
        chat_transport=_transport_factory(correct_until_last_n=2),
        model_config_override=_cfg(),
        persist=True,
    ))
    base = next(p for p in doc["points"] if p["baseline"])
    assert base["requested_context_tokens"] == 2000
    for p in doc["points"]:
        assert p["score"] == pytest.approx(0.833333)
    assert base["degradation_from_baseline"] == 0.0
    other = next(p for p in doc["points"] if not p["baseline"])
    assert other["degradation_from_baseline"] == 0.0


# ---------------------------------------------------------------------------
# Persistence round-trip + read model projection
# ---------------------------------------------------------------------------

def test_persistence_round_trip_through_read_model(runs_dir):
    doc = asyncio_run(run_context_suite(
        URL, MODEL,
        context_points=[2000, 4000],
        token_count=_wc,
        chat_transport=_transport_factory(),
        model_config_override=_cfg(),
        persist=True,
    ))
    rid = doc["context_run_id"]
    assert rid.startswith("ctx-")
    loaded = load_context_result(rid)
    assert loaded["run_id"] == rid
    assert loaded["supported_point_count"] == 2
    assert loaded["gap_point_count"] == 0
    # read model surfaces authoritative scores verbatim (no recompute)
    assert loaded["points"][0]["score"] == doc["points"][0]["score"]


def test_repeated_runs_preserved_separately(runs_dir):
    d1 = asyncio_run(run_context_suite(
        URL, MODEL, context_points=[2000], token_count=_wc,
        chat_transport=_transport_factory(), model_config_override=_cfg(), persist=True))
    d2 = asyncio_run(run_context_suite(
        URL, MODEL, context_points=[2000], token_count=_wc,
        chat_transport=_transport_factory(), model_config_override=_cfg(), persist=True))
    # independent run ids -> distinct artifact files, neither overwrites the other
    assert d1["context_run_id"] != d2["context_run_id"]
    assert art.artifact_path(d1["context_run_id"]).exists()
    assert art.artifact_path(d2["context_run_id"]).exists()


# ---------------------------------------------------------------------------
# Ownership isolation (no cross-model evidence leakage)
# ---------------------------------------------------------------------------

def test_ownership_isolation_between_models(runs_dir):
    doc_a = asyncio_run(run_context_suite(
        URL, "model-A", context_points=[2000], token_count=_wc,
        chat_transport=_transport_factory(), model_config_override=_cfg(model="model-A"), persist=True))
    doc_b = asyncio_run(run_context_suite(
        URL, "model-B", context_points=[2000], token_count=_wc,
        chat_transport=_transport_factory(), model_config_override=_cfg(model="model-B"), persist=True))

    fa = doc_a["configuration_fingerprint"]
    fb = doc_b["configuration_fingerprint"]
    assert fa and fb and fa != fb  # distinct fingerprints per model/config
    # each artifact carries only its own model identity
    la = load_context_result(doc_a["context_run_id"])
    lb = load_context_result(doc_b["context_run_id"])
    assert la["model_key"] == "model-A"
    assert lb["model_key"] == "model-B"
    assert la["configuration_fingerprint"] == fa
    assert lb["configuration_fingerprint"] == fb


# ---------------------------------------------------------------------------
# Operational failure captured distinctly (not a crash, not degradation)
# ---------------------------------------------------------------------------

def test_http_failure_captured_as_failed_status(runs_dir):
    async def _boom(client, url, payload):
        import httpx
        raise httpx.ConnectError("down")

    doc = asyncio_run(run_context_suite(
        URL, MODEL, context_points=[2000], token_count=_wc,
        chat_transport=_boom, model_config_override=_cfg(), persist=True))
    assert doc["points"][0]["status"] == "failed"
    assert doc["points"][0]["score"] is None
    assert doc["points"][0]["failure_reason"]


# ---------------------------------------------------------------------------
# Deterministic generation contract (temperature 0, reasoning off)
# ---------------------------------------------------------------------------

def test_generation_uses_temperature_zero_and_reasoning_off(runs_dir):
    seen = []

    async def _capture(client, url, payload):
        seen.append(payload)
        from src.context_corpus import requested_fact_keys, facts
        kv = dict(facts())
        content = "\n".join(f"{k}: {kv[k]}" for k in requested_fact_keys())
        return {"output": [{"type": "message", "content": content}],
                "stats": {"input_tokens": 10, "total_output_tokens": 10}}

    asyncio_run(run_context_suite(
        URL, MODEL, context_points=[2000], token_count=_wc,
        chat_transport=_capture, model_config_override=_cfg(), persist=True))
    assert seen
    for p in seen:
        assert p["temperature"] == 0.0
        assert p["reasoning"] == "off"


# ---------------------------------------------------------------------------
# Missing model -> clear error (no silent success)
# ---------------------------------------------------------------------------

def test_missing_model_raises(runs_dir):
    with pytest.raises(r.ContextRunError):
        asyncio_run(run_context_suite(
            URL, "", token_count=_wc, chat_transport=_transport_factory(), persist=True))
