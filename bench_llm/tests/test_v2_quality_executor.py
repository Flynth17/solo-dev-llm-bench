"""Tests for the live V2 quality executor (Act 11C-4C3).

These run the executor entirely OFFLINE against a stubbed LM Studio transport and
a stubbed configuration resolver -- no network, no persistence. They prove the
full pipeline (prompt -> request -> response -> extractor -> frozen validator) for
every suite, that aggregation matches the canonical corpus sizes, that extraction
failures stay observable, reasoning-control flagging, one-shot transport retry, and
that the executor never touches persistence by construction.
"""

import hashlib
import re
import sys
from pathlib import Path

import httpx
import pytest

# Offline but heavy (real async retry backoff, ~7-8s each). Excluded from the
# fast gate via `-m "not slow"`; retained in the full regression suite.
pytestmark = pytest.mark.slow

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import src.v2_quality_executor as ex
from src.quality import python_cases as _py
from src.quality import java_cases as _java
from src.quality import markdown_cases as _md
from src.quality import evidence_cases as _ev
from src.quality import drift_cases as _drift
from src.v2_quality_prompts import JAVA_SPECS as _JAVA_SPECS, evidence_scenarios


# ---------------------------------------------------------------------------
# Offline fakes
# ---------------------------------------------------------------------------

_PY_REF = "\n".join(_py.REFERENCE_SOLUTIONS.values())     # combined 10-function module


def _make_body(text: str, *, reason: bool = False) -> dict:
    """Build an LM Studio-shaped response body (message segment + optional reasoning)."""
    out: list[dict] = []
    if reason:
        out.append({"type": "reasoning", "text": "thinking..."})
    out.append({"type": "message", "text": text})
    return {
        "output": out,
        "stats": {
            "input_tokens": 100,
            "total_output_tokens": 200,
            "reasoning_output_tokens": 12 if reason else 0,
            "time_to_first_token_seconds": 0.02,
            "tokens_per_second": 500.0,
        },
    }


def _stub_config_resolver(url: str, model: str) -> dict:
    return {
        "model_key": model,
        "model_quantization": "Q5_K_M",          # a real quantization label (not a failure mode)
        "loaded_context": 262144,
        "model_max_context": 262144,
        "reasoning_mode": "off",
        "flash_attention": True,
        "offload_kv_cache_to_gpu": False,
        "eval_batch_size": 32,
        "physical_batch_size": 32,
        "parallel": 1,
        "kv_cache_k_quantization": None,
        "kv_cache_v_quantization": None,
    }


class FakeTransport:
    """Deterministic stand-in for the LM Studio HTTP transport.

    Returns a perfect answer per suite so the whole corpus grades at full marks --
    proving plumbing + aggregation offline. ``reason`` leaks a reasoning segment on
    every response (to exercise reasoning-honour flagging).
    """

    def __init__(self, *, reason: bool = False, calls: list | None = None):
        self.reason = reason
        self.calls = calls if calls is not None else []

    async def __call__(self, client, url, payload) -> dict:
        self.calls.append((url, payload))
        inp = payload.get("input", "")

        # Python (combined module).
        if "Implement ALL of the following functions in a single Python module" in inp:
            return _make_body(f"```python\n{_PY_REF}\n```\n", reason=self.reason)

        # Java (one per case, matched by its method signature).
        for spec in _JAVA_SPECS:
            if spec.method_signature in inp:
                ref = _java.REFERENCE_SOLUTIONS[spec.id]
                return _make_body(f"```java\n{ref}\n```\n", reason=self.reason)

        # Markdown (corrected document wrapped in a TILDE fence so inner backtick
        # code fences are not mistaken for the outer closing fence). The corrected
        # fixture ends with a newline, so the closing fence sits on its own line and
        # the inner text is preserved byte-for-byte.
        if "repairing a Markdown document" in inp:
            corrected = _md.load_corrected_fixture()
            return _make_body(f"~~~\n{corrected}~~~\n", reason=self.reason)

        # Evidence (one per scenario; graded via its own gradeable block).
        for cid, scenario_text in evidence_scenarios().items():
            if scenario_text in inp:
                return _make_body(_ev.EVID_FIXTURES[cid]["good"], reason=self.reason)

        # DRIFT-01.
        if "OPERATING POLICY" in inp:
            return _make_body(_drift.GOOD_RESPONSE, reason=self.reason)

        raise AssertionError(f"stub received an unexpected prompt:\n{inp[:200]}")


def _run_executor(monkeypatch, *, transport=None, reason=False):
    if transport is None:
        transport = FakeTransport(reason=reason)

    async def fake_resolve(url: str, model: str) -> dict:
        return _stub_config_resolver(url, model)

    monkeypatch.setattr(ex, "_resolve_model_config", fake_resolve)
    return asyncio_run(ex.run_v2_quality_live("http://localhost:1234", "test-model",
                                             chat_transport=transport))


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# End-to-end plumbing + aggregation (full corpus grades at full marks offline).
# ---------------------------------------------------------------------------

def test_full_corpus_pipeline_reaches_every_validator(monkeypatch):
    res = _run_executor(monkeypatch)
    # Aggregation derived from real validator results.
    assert res["logical_cases_attempted"] == 53
    assert res["result_entries"] == 141
    assert (res["checks_passed"], res["checks_total"]) == (166, 166)
    assert (res["v2_quality_checks_passed"], res["v2_quality_checks_total"]) == (166, 166)
    # All 25 requests were issued exactly once.
    assert len(res["requests"]) == 25


def test_python_reaches_python_extractor_and_validator(monkeypatch):
    res = _run_executor(monkeypatch)
    py_reqs = [r for r in res["requests"] if r["suite"] == "python"]
    assert len(py_reqs) == 1
    req = py_reqs[0]
    assert req["extraction_success"] is True
    assert "def split_camel(" in req["extracted_source"]
    # 58/58 checks across PY-01..PY-10, flat per-check results.
    assert (req["checks_passed"], req["checks_total"]) == (58, 58)
    assert all(cr["passed"] for cr in req["case_results"])


def test_one_java_request_reaches_java_extractor_and_validator(monkeypatch):
    res = _run_executor(monkeypatch)
    java_reqs = sorted([r for r in res["requests"] if r["suite"] == "java"],
                       key=lambda r: r["request_id"])
    assert [r["request_id"] for r in java_reqs] == [c.id for c in _JAVA_SPECS]
    # Each Java case extracts its own class Solution and passes all its checks.
    assert sum(r["checks_passed"] for r in java_reqs) == 52
    assert sum(r["checks_total"] for r in java_reqs) == 52
    sample = next(r for r in java_reqs if r["request_id"] == "JAVA-01")
    assert "class Solution" in sample["extracted_source"]


def test_markdown_reaches_extractor_and_validator(monkeypatch):
    res = _run_executor(monkeypatch)
    md_req = next(r for r in res["requests"] if r["suite"] == "markdown")
    assert md_req["extraction_success"] is True
    # Outer fence unwrapped; document itself byte-preserved (no repair by extraction).
    from src.quality import markdown_cases as _m
    assert md_req["extracted_source"] == _m.load_corrected_fixture()
    assert (md_req["checks_passed"], md_req["checks_total"]) == (20, 20)


def test_evidence_reaches_validator_per_case(monkeypatch):
    res = _run_executor(monkeypatch)
    ev_reqs = sorted([r for r in res["requests"] if r["suite"] == "evidence"],
                     key=lambda r: r["request_id"])
    expected_evid_ids = [f"EVID-0{i}" for i in range(1, 10)] + ["EVID-10"]
    assert sorted(r["request_id"] for r in ev_reqs) == sorted(expected_evid_ids)
    # Evidence grades per case (one CaseResult each) at full marks.
    assert sum(r["checks_passed"] for r in ev_reqs) == 30
    assert sum(r["checks_total"] for r in ev_reqs) == 30
    # Evidence is identity-passthrough: the stored raw response is the model's own
    # free-text answer, preserved verbatim for grading via its gradeable block.
    sample = next(r for r in ev_reqs if r["request_id"] == "EVID-01")
    assert isinstance(sample["raw_response"], str) and len(sample["raw_response"]) > 0


def test_drift_reaches_validator_unchanged(monkeypatch):
    res = _run_executor(monkeypatch)
    d_req = next(r for r in res["requests"] if r["suite"] == "drift")
    assert d_req["extraction_success"] is True
    # Raw response preserved exactly (no JSON normalisation / no answer extraction).
    assert d_req["raw_response"] == _drift.GOOD_RESPONSE
    assert (d_req["checks_passed"], d_req["checks_total"]) == (6, 6)


# ---------------------------------------------------------------------------
# Extraction failures remain visible and are never masked into a pass.
# ---------------------------------------------------------------------------

def test_extraction_failure_is_visible_and_not_masked(monkeypatch):
    class NoCodeTransport:
        async def __call__(self, client, url, payload):
            return _make_body("I am sorry, I cannot solve these tasks.")

    res = _run_executor(monkeypatch, transport=NoCodeTransport())
    py_req = next(r for r in res["requests"] if r["suite"] == "python")
    assert py_req["extraction_success"] is False
    assert py_req["extraction_failure_type"] == "extraction_failure"
    # An observable extraction-failure result was recorded (not a fabricated pass).
    assert any(cr["failure_type"] == "extraction_failure" for cr in py_req["case_results"])
    assert py_req["checks_passed"] == 0


def test_java_missing_solution_extraction_failure(monkeypatch):
    class NoSolution:
        async def __call__(self, client, url, payload):
            return _make_body("public static int helper() {\n    return 1;\n}\n")

    res = _run_executor(monkeypatch, transport=NoSolution())
    j_req = next(r for r in res["requests"] if r["suite"] == "java" and r["request_id"] == "JAVA-01")
    assert j_req["extraction_success"] is False
    assert j_req["extraction_failure_type"] == "extraction_failure"


# ---------------------------------------------------------------------------
# Reasoning policy = inherit: no override sent; observed/loaded state recorded.
# (Act 11C-4C4.4 -- the benchmark runs LM Studio exactly as loaded/configured.)
# ---------------------------------------------------------------------------

def test_request_payload_carries_no_reasoning_override(monkeypatch):
    # The benchmark must NOT turn reasoning on/off via the request: every payload
    # omits a ``reasoning`` field entirely (LM Studio uses its loaded config), and
    # the result documents the inherit policy.
    transport = FakeTransport(reason=False)
    res = _run_executor(monkeypatch, transport=transport)
    assert all("reasoning" not in p for _, p in transport.calls)
    assert len(transport.calls) == 25
    assert res["reasoning_policy"] == "inherit"


def test_loaded_reasoning_state_recorded_not_inferred(monkeypatch):
    # The stub resolver records a loaded reasoning state; the executor surfaces it
    # verbatim (never infers one). Here the stub reports "off".
    res = _run_executor(monkeypatch)
    assert res["loaded_reasoning_mode"] == "off"


def test_observed_reasoning_still_recorded_when_model_produces_it(monkeypatch):
    # Even though we inherit (no override), reasoning segments/tokens the model
    # actually produces are still observed and recorded per request -- not flagged.
    res = _run_executor(monkeypatch, reason=True)  # stub leaks a reasoning segment
    assert all(r["reasoning_observed_present"] is True for r in res["requests"])
    assert all(r["reasoning_tokens"] > 0 for r in res["requests"])
    # Inherit semantics: observed reasoning does NOT invalidate the run.
    assert res["validity"] in ("canonical", "incomplete")
    # The pipeline still completed and returned the (correct) benchmark results.
    assert (res["checks_passed"], res["checks_total"]) == (166, 166)


# ---------------------------------------------------------------------------
# Transport retry: one retry only, and only on genuine transport/server errors.
# ---------------------------------------------------------------------------

class OnceFlakyServer(FakeTransport):
    """FakeTransport that raises a genuine server 5xx once, then behaves perfectly."""

    def __init__(self):
        super().__init__()
        self._calls = 0

    async def __call__(self, client, url, payload) -> dict:
        self._calls += 1
        if self._calls == 1:
            resp = httpx.Response(503, request=httpx.Request("POST", url))
            raise httpx.HTTPStatusError("503", request=httpx.Request("POST", url), response=resp)
        return await super().__call__(client, url, payload)


def test_retry_once_on_server_5xx_then_succeeds(monkeypatch):
    transport = OnceFlakyServer()
    res = _run_executor(monkeypatch, transport=transport)
    # 25 normal requests + exactly one retry on the first (5xx) request = 26 transport invocations.
    assert transport._calls == 26
    assert (res["checks_passed"], res["checks_total"]) == (166, 166)


class OnceFlakyPerfect(FakeTransport):
    """FakeTransport that raises a genuine transport error once, then behaves perfectly."""

    def __init__(self, *, reason: bool = False):
        super().__init__(reason=reason)
        self._calls = 0

    async def __call__(self, client, url, payload) -> dict:
        self._calls += 1
        if self._calls == 1:
            raise httpx.ConnectError("connection refused")
        return await super().__call__(client, url, payload)


def test_retry_once_on_transport_error(monkeypatch):
    # Genuine transport error on the first request -> one retry -> full pass.
    res = _run_executor(monkeypatch, transport=OnceFlakyPerfect())
    assert (res["checks_passed"], res["checks_total"]) == (166, 166)


def test_no_retry_on_client_error_or_non_transport(monkeypatch):
    # HTTP 4xx must not be retried.
    async def client_err(client, url, payload):
        raise httpx.HTTPStatusError("400", request=httpx.Request("POST", url),
                                    response=httpx.Response(400, request=httpx.Request("POST", url)))

    with pytest.raises(httpx.HTTPStatusError):
        _run_executor(monkeypatch, transport=client_err)
    # Non-transport exceptions must not be retried either.
    async def value_err(client, url, payload):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        _run_executor(monkeypatch, transport=value_err)


# ---------------------------------------------------------------------------
# Model identity + configuration fingerprint / classification.
# ---------------------------------------------------------------------------

def test_configuration_fingerprint_is_deterministic_and_present(monkeypatch):
    res = _run_executor(monkeypatch)
    fp = res["configuration_fingerprint"]
    assert isinstance(fp, str) and len(fp) == 64 and re.fullmatch(r"[0-9a-f]{64}", fp)
    # Classification is canonical or incomplete (never invalid_reasoning under inherit).
    assert res["classification"] in ("canonical", "incomplete")


def test_model_identity_captured_from_resolver(monkeypatch):
    res = _run_executor(monkeypatch)
    assert res["model_identifier"] == "test-model"
    assert res["loaded_context"] == 262144
    assert res["model_quantization"] == "Q5_K_M"


# ---------------------------------------------------------------------------
# Per-request telemetry is captured.
# ---------------------------------------------------------------------------

def test_per_request_telemetry_captured(monkeypatch):
    res = _run_executor(monkeypatch)
    reqs = res["requests"]
    assert all("raw_prompt" in r and "raw_response" in r for r in reqs)
    assert all(r["duration_seconds"] >= 0 for r in reqs)
    assert all(r["prompt_tokens"] == 100 and r["completion_tokens"] == 200 for r in reqs)
    assert all(r["ttft_seconds"] is not None for r in reqs)
    # At least decode throughput surfaced from stats.
    assert any(r["decode_throughput_tokens_per_second"] is not None for r in reqs)


# ---------------------------------------------------------------------------
# Proof of no persistence: by construction (static) and by behaviour (runtime).
# ---------------------------------------------------------------------------

def test_executor_never_references_persistence_by_construction():
    src = Path(ex.__file__).read_text(encoding="utf-8")
    # Strip the leading module docstring so prose references (e.g. "does not touch
    # V2QualityStore in this paragraph") do not produce false positives.
    first = src.index('"""')
    second = src.index('"""', first + 3)
    code_only = src[:first] + src[second + 3:]
    for forbidden in ("V2QualityStore", "ResultsStore", "sqlite3", ".db", "save_run", "to_csv"):
        assert forbidden not in code_only, f"executor must not reference {forbidden}"


def test_executor_makes_no_db_or_csv_writes(monkeypatch):
    # Snapshot tracked data artifacts before/after a run; none should appear.
    data_dir = _ROOT / "data"
    def present():
        if not data_dir.exists():
            return set()
        return {p.name for p in data_dir.glob("*") if p.suffix in (".db", ".csv")}

    before = present()
    _run_executor(monkeypatch)
    after = present()
    assert after == before  # no new DB/CSV files created by the executor


def test_returned_object_has_no_db_path_or_store_keys(monkeypatch):
    res = _run_executor(monkeypatch)
    for key in ("db_path", "csv_path", "store", "results_path"):
        assert key not in res


# ---------------------------------------------------------------------------
# Regression coverage for the real LM Studio /api/v1/chat response shape.
#
# The live endpoint returns segments whose text lives under ``content`` (not the
# OpenAI-compatible ``text`` field). These lock that contract so a future edit to
# the parser cannot silently regress to reading the wrong field -- which would turn
# every model answer into an empty extraction failure. Items 1-5 map to the five
# behaviours enumerated in Act 11C-4C4.3.
# ---------------------------------------------------------------------------

def _content_body(text: str, *, reason: bool = False) -> dict:
    """Build a real LM Studio /api/v1/chat body: segments carry ``content``."""
    out: list[dict] = []
    if reason:
        out.append({"type": "reasoning", "content": "thinking..."})
    out.append({"type": "message", "content": text})
    return {
        "output": out,
        "stats": {
            "input_tokens": 100,
            "total_output_tokens": 200,
            "reasoning_output_tokens": 12 if reason else 0,
            "time_to_first_token_seconds": 0.02,
            "tokens_per_second": 500.0,
        },
    }


class _ContentFakeTransport:
    """Like FakeTransport, but emits the real LM Studio shape (``content``, not
    ``text``). Mirrors every suite's prompt matcher so the full pipeline can be
    driven on that exact response format -- proving the parser fix reaches the
    extractors + frozen validators end-to-end, not just in isolation."""

    def __init__(self, *, reason: bool = False):
        self.reason = reason

    async def __call__(self, client, url, payload) -> dict:
        inp = payload.get("input", "")
        if "Implement ALL of the following functions in a single Python module" in inp:
            return _content_body(f"```python\n{_PY_REF}\n```\n", reason=self.reason)
        for spec in _JAVA_SPECS:
            if spec.method_signature in inp:
                ref = _java.REFERENCE_SOLUTIONS[spec.id]
                return _content_body(f"```java\n{ref}\n```\n", reason=self.reason)
        if "repairing a Markdown document" in inp:
            corrected = _md.load_corrected_fixture()
            return _content_body(f"~~~\n{corrected}~~~\n", reason=self.reason)
        for cid, scenario_text in evidence_scenarios().items():
            if scenario_text in inp:
                return _content_body(_ev.EVID_FIXTURES[cid]["good"], reason=self.reason)
        if "OPERATING POLICY" in inp:
            return _content_body(_drift.GOOD_RESPONSE, reason=self.reason)
        raise AssertionError(f"stub received an unexpected prompt:\n{inp[:200]}")


def test_content_shaped_stream_reaches_every_validator(monkeypatch):
    # End-to-end regression guard: drive the whole pipeline on a real-shape response
    # stream (segments carry ``content``) and assert it still grades at full marks,
    # i.e. a content answer reaches each extractor + frozen validator unchanged.
    res = _run_executor(monkeypatch, transport=_ContentFakeTransport())
    assert len(res["requests"]) == 25
    assert all(r["extraction_success"] for r in res["requests"])
    assert (res["checks_passed"], res["checks_total"]) == (166, 166)


def test_response_text_reads_message_content_field():
    # Item 1: {"type":"message","content":"banana"} -> "banana".
    body = {"output": [{"type": "message", "content": "banana"}], "stats": {}}
    answer, reasoning = ex._response_text(body)
    assert answer == "banana"
    # No reasoning segment and no reasoning stat -> not determinable from this body.
    assert reasoning is None


def test_response_text_falls_back_to_text_field():
    # Item 2: {"type":"message","text":"banana"} -> "banana" (OpenAI-compatible).
    body = {"output": [{"type": "message", "text": "banana"}], "stats": {}}
    answer, _ = ex._response_text(body)
    assert answer == "banana"


def test_reasoning_content_segment_detected_and_message_still_parsed():
    # Item 3: a reasoning segment also carries ``content``; message text is graded.
    segs = [
        {"type": "reasoning", "content": "let me think this through"},
        {"type": "message", "content": "ok"},
    ]
    body = {"output": segs, "stats": {}}
    answer, reasoning = ex._response_text(body)
    assert answer == "ok"            # only the message segment forms the graded text
    assert reasoning is True          # reasoning observed (from a content segment)
    assert ex._seg_text(segs[0]) == "let me think this through"
    assert ex._seg_text(segs[1]) == "ok"


def test_reasoning_off_is_determinable_and_honoured_with_content():
    # Item 4: message-only content + reasoning_output_tokens==0 -> determinable False.
    body = {"output": [{"type": "message", "content": "banana"}],
            "stats": {"reasoning_output_tokens": 0}}
    answer, reasoning = ex._response_text(body)
    assert answer == "banana"
    # False means no reasoning produced -> honoured when OFF was requested.
    assert reasoning is False


@pytest.mark.parametrize("body", [
    {"output": [{}]},                  # segment missing type / text / content
    {"output": [42, None, "str"]},     # non-dict / junk segments interspersed
    {"output": []},                    # empty output list
    {},                                # no output key at all
])
def test_malformed_or_empty_segments_handled_safely(body):
    # Item 5: malformed/empty responses never raise; the parsed answer is None.
    result = ex._response_text(body)
    assert result[0] is None
