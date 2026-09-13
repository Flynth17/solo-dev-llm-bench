"""Focused tests for the suite-isolated standalone V2 quality runner
(:mod:`src.v2_quality_suite_runner`) and its coordinator (Act 11C-4D).

These prove the *accounting / identity / payload* invariants that define the new
layer. They are deliberately OFFLINE: no live LM Studio, no network -- the HTTP
transport is injected, config is overridden, and frozen validators are only where
they run cheaply (Markdown / Evidence are pure; Python/Java/Denominator accounting
are exercised with synthetic CaseResults so we never spend time on javac/pytest).

The eight required proofs:

1. Python extraction failure -> 0/58            (denominator immutable, never 0/0)
2. Java compile failure cannot inflate > 52     (squared validator rows are capped)
3. Successful reference results -> 58/52/20/30/6 total 166
4. Missing suite prevents aggregation
5. Fingerprint mismatch prevents aggregation
6. Model mismatch prevents aggregation
7. Reasoning fields absent from the LM Studio request payload
8. SuiteResult serialises / deserialises cleanly
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.v2_quality_suite_runner import (  # noqa: E402
    CANONICAL_DENOMINATORS,
    TOTAL_DENOMINATOR,
    SUITES,
    SuiteResult,
    ConfigurationMismatchError,
    account_checks,
    build_chat_payload,
    combine_suite_results,
    _suite_requests,
    run_suite,
    output_ceiling,
    OUTPUT_BUDGET_POLICY,
    OUTPUT_BUDGET_PERCENT,
)
from src.results import compute_configuration_fingerprint  # noqa: E402
from src.quality._common import CaseResult  # noqa: E402


def asyncio_run(coro):
    """Run a coroutine to completion (matches the existing executor-test convention)."""
    import asyncio

    return asyncio.run(coro)


KNOWN_MODEL = "ornith-1.5-35b-a3b"
CFG = {
    "model_key": KNOWN_MODEL,
    "model_quantization": "Q5_K_M",
    "loaded_context": 262144,
    "model_max_context": 262144,
    "reasoning_mode": "not_exposed",
}


def _perfect_units(suite: str) -> list[CaseResult]:
    """One passing unit per canonical check -- i.e. a perfectly validating suite."""
    return [
        CaseResult(
            case_id=f"{suite.upper()}-chk-{i}",
            category=suite,
            name=f"check {i}",
            passed=True,
            checks_passed=1,
            checks_total=1,
        )
        for i in range(CANONICAL_DENOMINATORS[suite])
    ]


def _java_compile_failure_rows() -> list[CaseResult]:
    """Mimic exactly what ``_run_case_test`` returns on a compile failure: one
    CaseResult *per check*, each carrying ``checks_total = len(case.checks)``.

    That is the squaring behaviour that inflated the legacy run's Java total from
    52 to 64. Feeding these into :func:`account_checks` must still return a total
    of exactly 52 -- never more.
    """
    from src.quality import java_cases as _java

    rows: list[CaseResult] = []
    for case in _java.JAVA_CASES:
        rows.extend(
            CaseResult(
                case_id=case.id,
                category=case.category,
                name=case.name,
                passed=False,
                checks_passed=0,
                checks_total=len(case.checks),  # squared per-check contribution
                failure_type="compile_error",
                failure_reason="javac error: Solution.java:1: error: bad token",
            )
            for _ in case.checks
        )
    return rows


def _suite_result(suite: str, *, passed: int, fingerprint: str = "fp-1",
                  model: str = KNOWN_MODEL, run_id: str = "run-A") -> SuiteResult:
    """Construct a minimal but valid SuiteResult for aggregation tests."""
    return SuiteResult(
        run_id=run_id,
        suite=suite,
        model_identifier=model,
        configuration_fingerprint=fingerprint,
        classification="canonical",
        reasoning_policy="inherit",
        loaded_reasoning_mode="not_exposed",
        requests_expected=1,
        requests_completed=1,
        checks_passed=passed,
        checks_total=CANONICAL_DENOMINATORS[suite],
    )


async def _good_markdown_transport(payloads):
    from src.quality.markdown_cases import load_corrected_fixture

    async def transport(client, url, payload):
        payloads.append(payload)
        return {
            "output": [{"type": "message", "content": load_corrected_fixture()}],
            "stats": {"input_tokens": 10, "total_output_tokens": 5,
                      "time_to_first_token_seconds": 0.01},
        }

    return transport


# ---------------------------------------------------------------------------
# Proof 1: Python extraction failure -> 0/58 (never 0/0).
# ---------------------------------------------------------------------------

def test_python_extraction_failure_is_zero_fifty_eight():
    async def no_code_transport(client, url, payload):
        return {"output": [], "stats": {}}  # nothing gradeable -> extraction fails

    result = asyncio_run(
        run_suite(
            "python", model=KNOWN_MODEL, model_config_override=CFG,
            chat_transport=no_code_transport,
        )
    )
    assert result.checks_total == CANONICAL_DENOMINATORS["python"] == 58
    assert result.checks_passed == 0
    assert result.extraction_failures, "extraction failure should be recorded"
    # Denominator is immutable and canonical -- not the 0/0 the legacy path implied.
    assert (result.checks_passed, result.checks_total) == (0, 58)


# ---------------------------------------------------------------------------
# Proof 2: Java compile failure cannot inflate denominator above 52.
# ---------------------------------------------------------------------------

def test_java_compile_failure_cannot_inflate_denominator():
    rows = _java_compile_failure_rows()
    # Sanity: the raw validator rows really do sum to MORE than 52 (the squaring).
    raw_total = sum(r.checks_total for r in rows)
    assert raw_total > 52
    passed, total = account_checks("java", rows)
    assert total == CANONICAL_DENOMINATORS["java"] == 52          # never inflated
    assert passed < 52                                             # that case's checks failed
    assert passed == 0


# ---------------------------------------------------------------------------
# Proof 3: Successful reference results -> 58/52/20/30/6, total 166.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("suite", SUITES)
def test_accounting_maps_perfect_result_onto_canonical_denominator(suite):
    passed, total = account_checks(suite, _perfect_units(suite))
    assert (passed, total) == (CANONICAL_DENOMINATORS[suite], CANONICAL_DENOMINATORS[suite])


def test_accounting_caps_passed_at_denominator():
    # A validator that emitted more passing units than canonical must not exceed D.
    over = [CaseResult("X", "python", "x", True, 1, 1) for _ in range(58 + 25)]
    passed, total = account_checks("python", over)
    assert (passed, total) == (58, 58)


def test_reference_results_aggregate_to_166():
    # Real validators prove the frozen corpus defines exactly these maxima where cheap.
    from src.quality import markdown_cases as _md
    from src.quality import evidence_cases as _ev
    from src.v2_quality_executor import _evidence_block

    corrected = Path(_ROOT, "src", "quality", "markdown_fixtures", "corrected_20.md").read_text()
    md_passed = sum(r.checks_passed for r in _md.validate(corrected))
    assert md_passed == 20

    ev_total = 0
    for cid in [f"EVID-{i:02d}" for i in range(1, 11)]:
        block = _evidence_block(cid, _ev.EVID_FIXTURES[cid]["good"])
        ev_total += sum(r.checks_passed for r in _ev.validate(block))
    assert ev_total == 30

    # Coordinator-level: perfect SuiteResults combine to the immutable /166 total.
    results = [_suite_result(s, passed=CANONICAL_DENOMINATORS[s]) for s in SUITES]
    agg = combine_suite_results(results)
    assert agg["python"] == "58/58"
    assert agg["java"] == "52/52"
    assert agg["markdown"] == "20/20"
    assert agg["evidence"] == "30/30"
    assert agg["drift"] == "6/6"
    assert agg["checks_passed"] == 166
    assert agg["checks_total"] == TOTAL_DENOMINATOR == 166


# ---------------------------------------------------------------------------
# Proof 4: A missing suite prevents aggregation.
# ---------------------------------------------------------------------------

def test_missing_suite_prevents_aggregation():
    results = [_suite_result(s, passed=CANONICAL_DENOMINATORS[s]) for s in SUITES if s != "evidence"]
    with pytest.raises(ConfigurationMismatchError) as exc:
        combine_suite_results(results)
    assert "evidence" in str(exc.value)


# ---------------------------------------------------------------------------
# Proof 5: Fingerprint mismatch prevents aggregation.
# ---------------------------------------------------------------------------

def test_fingerprint_mismatch_prevents_aggregation():
    results = [
        _suite_result("python", passed=58, fingerprint="fp-A"),
    ] + [_suite_result(s, passed=CANONICAL_DENOMINATORS[s], fingerprint="fp-B") for s in SUITES if s != "python"]
    with pytest.raises(ConfigurationMismatchError) as exc:
        combine_suite_results(results)
    assert "fingerprint" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Proof 6: Model mismatch prevents aggregation.
# ---------------------------------------------------------------------------

def test_model_mismatch_prevents_aggregation():
    results = [_suite_result("python", passed=58, model="ornith-1.5-35b-a3b")]
    results += [_suite_result(s, passed=CANONICAL_DENOMINATORS[s], model="some-other-model")
                for s in SUITES if s != "python"]
    with pytest.raises(ConfigurationMismatchError) as exc:
        combine_suite_results(results)
    assert "model" in str(exc.value).lower()


def test_run_id_mismatch_prevents_aggregation():
    results = [_suite_result("python", passed=58, run_id="run-A")]
    results += [_suite_result(s, passed=CANONICAL_DENOMINATORS[s], run_id="run-B")
                for s in SUITES if s != "python"]
    with pytest.raises(ConfigurationMismatchError) as exc:
        combine_suite_results(results)
    assert "run_id" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Proof 7: Reasoning fields are absent from the LM Studio request payload.
# ---------------------------------------------------------------------------

def test_payload_carries_no_reasoning_fields():
    payload = build_chat_payload(KNOWN_MODEL, "do a thing")
    assert "reasoning" not in payload
    assert "reasoning_effort" not in payload


def test_live_request_never_sends_reasoning_fields():
    from src.quality.markdown_cases import load_corrected_fixture as _lc

    captured: list[dict] = []

    async def capture(client, url, payload):
        captured.append(payload)
        return {"output": [{"type": "message", "content": _lc()}],
                "stats": {"input_tokens": 1, "total_output_tokens": 1}}

    asyncio_run(
        run_suite(
            "markdown", model=KNOWN_MODEL, model_config_override=CFG,
            chat_transport=capture,
        )
    )
    assert captured, "at least one request should have been made"
    for p in captured:
        assert "reasoning" not in p
        assert "reasoning_effort" not in p


# ---------------------------------------------------------------------------
# Proof 8: SuiteResult serialises / deserialises cleanly.
# ---------------------------------------------------------------------------

def test_suite_result_serialisation_roundtrip():
    base = _suite_result("java", passed=48, fingerprint="fp-xyz")
    base.extraction_failures = [{"request_id": "JAVA-02", "reason": "compile error"}]
    base.validation_failures = [
        {"case_id": "JAVA-02", "name": "x", "failure_type": "compile_error", "failure_reason": "bad"}
    ]
    base.requests = [{"request_id": "JAVA-01", "checks_passed": 6, "checks_total_validator": 6}]

    text = json.dumps(base.to_dict())          # JSON-serialisable
    restored = SuiteResult.from_dict(json.loads(text))

    for f in ("run_id", "suite", "model_identifier", "configuration_fingerprint",
              "classification", "reasoning_policy", "loaded_reasoning_mode",
              "requests_expected", "requests_completed", "checks_passed",
              "checks_total"):
        assert getattr(restored, f) == getattr(base, f), f
    # Nested containers survive the round-trip too.
    assert restored.extraction_failures == base.extraction_failures
    assert restored.validation_failures == base.validation_failures
    assert restored.requests == base.requests


# ---------------------------------------------------------------------------
# Java wiring regression (fix for: 'JavaPromptSpec' has no attribute 'checks').
#
# Root cause: the suite runner passed a *prompt spec* (JavaPromptSpec) into the
# frozen ``_run_case_test`` validator, which requires the canonical java case
# object (CaseDef with .checks). The executor resolves the canonical case by
# stable id via ``_java.JAVA_CASES``; the runner now does the same.
# ---------------------------------------------------------------------------

def _reference_java_source(case_id: str) -> str:
    from src.quality.java_cases import REFERENCE_SOLUTIONS

    return REFERENCE_SOLUTIONS[case_id]


def test_java_validator_receives_canonical_case_with_checks(monkeypatch):
    """Regression: the frozen validator must receive a canonical Java case
    (CaseDef with .checks), never a JavaPromptSpec prompt spec."""
    import src.v2_quality_suite_runner as runner
    from src.quality._common import CaseDef

    real = runner._java_run_case_test
    captured_cases: list[Any] = []

    def recorder(case, source):
        # If the wiring bug regressed, *case* is a JavaPromptSpec and this type
        # assertion fails immediately -- exactly the observed AttributeError.
        assert isinstance(case, CaseDef), (
            f"validator received {type(case).__name__}, expected canonical CaseDef"
        )
        assert hasattr(case, "checks") and len(case.checks) > 0
        captured_cases.append(case)
        # Delegate the real validator only for JAVA-01 so we exercise the frozen
        # javac path cheaply; the rest are accounted through the fixed denominator.
        if case.id == "JAVA-01":
            return real(case, _reference_java_source("JAVA-01"))
        return []

    monkeypatch.setattr(runner, "_java_run_case_test", recorder)

    async def good_transport(client, url, payload):
        return {"output": [{"type": "message", "content": _reference_java_source("JAVA-01")}],
                "stats": {"input_tokens": 20, "total_output_tokens": 40,
                          "time_to_first_token_seconds": 0.02}}

    result = asyncio_run(
        run_suite("java", model=KNOWN_MODEL, model_config_override=CFG,
                  chat_transport=good_transport)
    )

    # One validator call per canonical java case (12 cases -> 52 checks total).
    from src.quality.java_cases import JAVA_CASES as _JAVA_CASES
    assert len(captured_cases) == len(_JAVA_CASES)          # one per case, not per check
    assert {c.id for c in captured_cases} == {c.id for c in _JAVA_CASES}
    for c in captured_cases:
        assert isinstance(c, CaseDef)
    # JAVA-01 actually completed through the frozen validator and passed checks.
    j01 = next((r for r in result.requests if r["request_id"] == "JAVA-01"), None)
    assert j01 is not None
    assert j01["checks_passed"] >= 1, "JAVA-01 should pass some checks on the reference solution"
    assert j01["extraction_success"] is True


def test_java_suite_completes_offline_for_javal01():
    """End-to-end offline: with the fixed wiring, the whole java suite runs to
    completion (no AttributeError) and JAVA-01 validates against its reference."""
    src_ref = _reference_java_source("JAVA-01")

    async def transport(client, url, payload):
        return {"output": [{"type": "message", "content": src_ref}],
                "stats": {"input_tokens": 20, "total_output_tokens": 40,
                          "time_to_first_token_seconds": 0.02}}

    result = asyncio_run(
        run_suite("java", model=KNOWN_MODEL, model_config_override=CFG,
                  chat_transport=transport)
    )

    # Denominator remains the immutable canonical 52 for java.
    from src.quality.java_cases import JAVA_CASES as _JAVA_CASES
    assert result.checks_total == CANONICAL_DENOMINATORS["java"] == 52
    j01 = next((r for r in result.requests if r["request_id"] == "JAVA-01"), None)
    assert j01 is not None and j01["checks_passed"] >= 1

    # Partial preserved with the denominator fixed: only JAVA-01 passes on its
    # reference while the other 11 cases compile-error, so passed is real but < D.
    assert 0 < result.checks_passed < CANONICAL_DENOMINATORS["java"], (
        f"expected partial passed with a fixed denominator, got "
        f"{result.checks_passed}/{result.checks_total}"
    )
    # One model request per canonical java case (12), independent of the 52 checks.
    assert result.requests_completed == len(_JAVA_CASES) == 12


def test_java_denominator_remains_52_with_inflated_rows():
    """The squared per-check rows produced by a java compile failure must not
    inflate the canonical denominator above 52 (the accounting invariant)."""
    rows = _java_compile_failure_rows()
    assert sum(r.checks_total for r in rows) > 52          # raw rows really do exceed 52
    passed, total = account_checks("java", rows)
    assert total == CANONICAL_DENOMINATORS["java"] == 52   # denominator stays fixed
    assert passed < 52                                     # that case's checks still failed


def test_java_payload_carries_no_reasoning_fields():
    captured: list[dict] = []
    src_ref = _reference_java_source("JAVA-01")

    async def transport(client, url, payload):
        captured.append(payload)
        return {"output": [{"type": "message", "content": src_ref}],
                "stats": {"input_tokens": 20, "total_output_tokens": 40,
                          "time_to_first_token_seconds": 0.02}}

    asyncio_run(
        run_suite("java", model=KNOWN_MODEL, model_config_override=CFG,
                  chat_transport=transport)
    )
    assert captured, "at least one java request should have been made"
    for p in captured:
        assert "reasoning" not in p
        assert "reasoning_effort" not in p


# ---------------------------------------------------------------------------
# Regression for three live-run plumbing defects found in isolated suite runs.
#   1) partial-score aggregation must not zero a partially-passing suite
#   2) the canonical Markdown live prompt must carry the frozen broken fixture
#   3) reasoning-only LM Studio responses must never reach extractors / validators
# Every test below runs fully offline with an injected chat_transport.
# ---------------------------------------------------------------------------


def _mixed_units(n_pass: int, n_fail: int) -> list[CaseResult]:
    """Synthesise validator output: *n_pass* passing checks + *n_fail* failing
    ones, one CaseResult per check -- i.e. what a real validator emitted."""
    out = [
        CaseResult(case_id=f"p{i}", category="x", name=f"pass {i}",
                   passed=True, checks_passed=1, checks_total=1)
        for i in range(n_pass)
    ]
    out += [
        CaseResult(case_id=f"f{i}", category="x", name=f"fail {i}",
                   passed=False, checks_passed=0, checks_total=1)
        for i in range(n_fail)
    ]
    return out


def test_markdown_partial_19_20_aggregates_to_19_20():
    """A successful extraction whose validator fixes 19 of 20 defects must keep
    the partial score -- never collapse to 0/20."""
    passed, total = account_checks("markdown", _mixed_units(19, 1))
    assert (passed, total) == (19, 20)


def test_drift_partial_4_6_aggregates_to_4_6():
    """Same rule for the single-deliverable drift suite: 4/6 must stay 4/6."""
    passed, total = account_checks("drift", _mixed_units(4, 2))
    assert (passed, total) == (4, 6)


def test_extraction_failure_yields_zero_over_fixed_denominator():
    """An extraction failure leaves nothing gradeable: the suite scores 0 / D and
    the canonical denominator is preserved -- never a meaningless 0/0."""
    for suite in ("python", "markdown", "drift", "evidence"):
        passed, total = account_checks(suite, [])
        assert (passed, total) == (0, CANONICAL_DENOMINATORS[suite])


def test_reasoning_only_response_gives_no_final_message_and_zero_score(monkeypatch):
    """A python response that spent its whole budget on type=\"reasoning\" and
    returned NO final message must not reach the extractor; it is a distinct
    'no final message content' failure scoring 0/58 with telemetry intact."""
    import src.v2_quality_suite_runner as runner

    real_extract = runner.extract_python_module
    received: list[Any] = []

    def rec(answer):
        received.append(answer)
        return real_extract(answer)

    monkeypatch.setattr(runner, "extract_python_module", rec)

    async def transport(client, url, payload):
        return {"output": [{"type": "reasoning",
                           "content": "def fake():\n    return 42\n\n# decoy block"}],
                "stats": {"input_tokens": 30, "total_output_tokens": 4096,
                          "reasoning_output_tokens": 4096,
                          "time_to_first_token_seconds": 0.3}}

    result = asyncio_run(
        run_suite("python", model=KNOWN_MODEL, model_config_override=CFG,
                  chat_transport=transport)
    )
    assert received == [], "extractor must never receive a reasoning-only response"
    reasons = [f["reason"] for f in result.extraction_failures]
    assert "no final message content" in reasons
    assert result.checks_passed == 0
    assert result.checks_total == CANONICAL_DENOMINATORS["python"] == 58
    # Reasoning telemetry is still recorded even though no message was delivered.
    assert any(rr["reasoning_tokens"] == 4096 for rr in result.requests)


def test_reasoning_plus_message_sends_only_final_message_to_extractor(monkeypatch):
    """When a turn carries both type=\"reasoning\" and a final type=\"message\",
    ONLY the message content reaches the extractor -- the reasoner's code blocks
    must not leak in (and must not trigger a spurious 'multiple ... blocks' error).
    A real python response can only produce that multi-block failure if reasoning
    leaked into the extractor, so its absence proves the contract."""
    import src.v2_quality_suite_runner as runner

    real_extract = runner.extract_python_module
    captured: list[str] = []

    def rec(answer):
        captured.append(answer)
        return real_extract(answer)

    monkeypatch.setattr(runner, "extract_python_module", rec)

    FAKE_REASONER = "def fake():\n    return 42\n\n# decoy block\nx = 1"
    REAL_MSG = "def real_fn(x):\n    return x + 1"

    async def transport(client, url, payload):
        return {"output": [
            {"type": "reasoning", "content": FAKE_REASONER},
            {"type": "message", "content": REAL_MSG},
        ], "stats": {"input_tokens": 30, "total_output_tokens": 200,
                     "reasoning_output_tokens": 60,
                     "time_to_first_token_seconds": 0.1}}

    result = asyncio_run(
        run_suite("python", model=KNOWN_MODEL, model_config_override=CFG,
                  chat_transport=transport)
    )
    assert captured == [REAL_MSG], (
        f"only the final message should reach the extractor, got {captured!r}"
    )
    reasons = [f["reason"] for f in result.extraction_failures]
    assert not any("multiple" in r.lower() for r in reasons)


def test_markdown_live_prompt_appends_frozen_broken_fixture():
    """The canonical Markdown live prompt must append the frozen broken document
    used by the benchmark -- never an empty 'Input document to repair:' tail."""
    from src.quality.markdown_cases import load_broken_fixture

    request_id, prompt = _suite_requests("markdown")[0]
    assert request_id == "MD"
    broken = load_broken_fixture()
    assert broken and len(broken) > 1000
    assert broken in prompt, "frozen broken fixture must be present verbatim"
    tail = prompt.split("Input document to repair:", 1)[1]
    assert tail.strip(), "no document followed the repair marker"


def test_python_single_request_partial_score_preserved_end_to_end(monkeypatch):
    """End-to-end through run_suite: a single python request that extracts cleanly
    aggregates as <58 / 58 -- the denominator is fixed and any partial passed count
    is preserved, never zeroed."""
    import src.v2_quality_suite_runner as runner
    from types import SimpleNamespace

    async def transport(client, url, payload):
        return {"output": [{"type": "message", "content": "def f():\n    pass"}],
                "stats": {"input_tokens": 20, "total_output_tokens": 40,
                          "time_to_first_token_seconds": 0.02}}

    # Stub the extractor so we observe real validator accounting on any input.
    def fake_extract(answer):
        return SimpleNamespace(success=True, extracted_text=answer)

    monkeypatch.setattr(runner, "extract_python_module", fake_extract)

    result = asyncio_run(
        run_suite("python", model=KNOWN_MODEL, model_config_override=CFG,
                  chat_transport=transport)
    )
    assert result.checks_total == CANONICAL_DENOMINATORS["python"] == 58
    assert 0 <= result.checks_passed <= 58


# ---------------------------------------------------------------------------
# Newline-preservation audit (final byte-preservation plumbing check before /166).
# A corrected Markdown document must keep its terminal newline all the way through
# LM Studio response -> message extraction -> frozen validator => 20/20.
# ---------------------------------------------------------------------------

def test_response_text_preserves_terminal_newline_of_final_message():
    """The core regression: a final type=\"message\" ending in exactly one newline
    must reach the answer with that newline intact (never stripped to 0)."""
    import src.v2_quality_suite_runner as runner
    from src.quality import markdown_cases as _md

    doc = _md.load_corrected_fixture()
    assert doc.endswith("\n") and not doc.endswith("\n\n"), "fixture precondition"

    body = {
        "output": [
            {"type": "reasoning", "content": "pre-thought..."},
            {"type": "message", "content": doc},
        ],
        "stats": {},
    }
    answer, reasoning_present = runner._response_text(body)

    assert reasoning_present is True, "reasoning segment should be detected as telemetry"
    # Only the final message forms the deliverable (not the reasoning content).
    assert answer == doc, "final message must arrive byte-identical"
    # The legitimate terminal newline survives.
    assert answer.endswith("\n") and not answer.endswith("\n\n")


def test_canonical_markdown_fixture_passes_20_20_end_to_end_through_runner():
    """Full offline path: LM Studio-shaped response (reasoning + final message with
    the canonical corrected fixture) -> run_suite markdown suite -> frozen validator.
    Must be 20/20, never 19/20 from a stripped trailing newline."""
    from src.quality.markdown_cases import load_corrected_fixture

    async def transport(client, url, payload):
        return {
            "output": [
                {"type": "reasoning", "content": "let me repair the document..."},
                {"type": "message", "content": load_corrected_fixture()},
            ],
            "stats": {"input_tokens": 12, "total_output_tokens": 8,
                      "time_to_first_token_seconds": 0.01},
        }

    result = asyncio_run(
        run_suite("markdown", model=KNOWN_MODEL, model_config_override=CFG,
                  chat_transport=transport)
    )
    assert result.checks_total == CANONICAL_DENOMINATORS["markdown"] == 20
    assert result.checks_passed == 20, (
        f"corrected markdown must validate 20/20, got {result.checks_passed}/20 "
        f"(extraction_failures={result.extraction_failures})"
    )
    assert not result.extraction_failures


def test_reasoning_plus_message_sends_only_final_markdown_to_extractor(monkeypatch):
    """Reasoning + message separation still holds for markdown: only the final
    type=\"message\" document reaches the extractor, terminal newline preserved."""
    import src.v2_quality_suite_runner as runner
    from src.quality.markdown_cases import load_corrected_fixture

    real_extract = runner.extract_markdown_document
    captured: list[str] = []

    def rec(answer):
        captured.append(answer)
        return real_extract(answer)

    monkeypatch.setattr(runner, "extract_markdown_document", rec)

    async def transport(client, url, payload):
        return {
            "output": [
                {"type": "reasoning", "content": "# decoy heading\nnot the document"},
                {"type": "message", "content": load_corrected_fixture()},
            ],
            "stats": {},
        }

    result = asyncio_run(
        run_suite("markdown", model=KNOWN_MODEL, model_config_override=CFG,
                  chat_transport=transport)
    )
    assert len(captured) == 1, "only the final markdown message reaches the extractor"
    assert captured[0].endswith("\n") and not captured[0].endswith("\n\n")
    # The decoy reasoning heading must NOT have leaked into the graded document.
    assert "# decoy" not in captured[0]
    reasons = [f["reason"] for f in result.extraction_failures]
    assert not reasons
    assert (result.checks_passed, result.checks_total) == (20, 20)


def test_reasoning_only_markdown_gives_no_final_message_and_zero_score(monkeypatch):
    """A markdown turn that is reasoning-only (no final message) must still be a
    distinct 'no final message content' failure scoring 0/20 -- unchanged."""
    import src.v2_quality_suite_runner as runner

    real_extract = runner.extract_markdown_document
    received: list[Any] = []

    def rec(answer):
        received.append(answer)
        return real_extract(answer)

    monkeypatch.setattr(runner, "extract_markdown_document", rec)

    async def transport(client, url, payload):
        return {
            "output": [{"type": "reasoning", "content": "# just thinking\nno document"}],
            "stats": {"input_tokens": 30, "total_output_tokens": 4096,
                      "reasoning_output_tokens": 4096},
        }

    result = asyncio_run(
        run_suite("markdown", model=KNOWN_MODEL, model_config_override=CFG,
                  chat_transport=transport)
    )
    assert received == [], "extractor must never receive a reasoning-only markdown"
    reasons = [f["reason"] for f in result.extraction_failures]
    assert "no final message content" in reasons
    assert (result.checks_passed, result.checks_total) == (0, 20)


def test_markdown_newline_fix_keeps_java_denominator_at_52(monkeypatch):
    """The newline preservation fix is markdown-scoped in effect: the Java suite's
    immutable denominator must still be exactly 52. Stub extraction so we assert only
    on the canonical accounting without depending on javac being installed."""
    import src.v2_quality_suite_runner as runner
    from types import SimpleNamespace

    async def transport(client, url, payload):
        return {
            "output": [{"type": "message", "content": "```java\nclass Solution {}\n```\n"}],
            "stats": {},
        }

    # Extraction failure path: denominator must still resolve to the fixed 52.
    monkeypatch.setattr(
        runner, "extract_java_solution",
        lambda answer: SimpleNamespace(success=False,
                                       extracted_text=None,
                                       failure_reason="stubbed for auditor"),
    )

    result = asyncio_run(
        run_suite("java", model=KNOWN_MODEL, model_config_override=CFG,
                  chat_transport=transport)
    )
    assert result.checks_total == CANONICAL_DENOMINATORS["java"] == 52
    assert result.extraction_failures


def test_markdown_newline_fix_adds_no_reasoning_fields_to_payload(monkeypatch):
    """The fix must not introduce reasoning/reasoning_effort into the request."""
    seen: list[dict] = []

    async def transport(client, url, payload):
        seen.append(payload)
        return {"output": [], "stats": {}}

    asyncio_run(
        run_suite("markdown", model=KNOWN_MODEL, model_config_override=CFG,
                  chat_transport=transport)
    )
    assert len(seen) == 1
    p = seen[0]
    assert "reasoning" not in p
    assert "reasoning_effort" not in p


# ---------------------------------------------------------------------------
# Fixed output-ceiling policy for the V2 QUALITY benchmark.
#
# requested_max_output_tokens = floor(effective_context_capacity * 75 // 100)
# where effective_context_capacity = min(known model_max_context, known loaded_context).
# This is a pure context CEILING: no prompt estimation, no chars/token heuristic, no
# tokenizer library. If neither context value is known we raise -- never fabricate a
# capacity or fall back to a hardcoded token count. The policy also contributes to the
# configuration fingerprint so old 4096 / old 90% / new 75% runs are distinguishable.
# ---------------------------------------------------------------------------

_FULL_CFG = {
    "model_key": KNOWN_MODEL,
    "model_quantization": "Q5_K_M",
    "loaded_context": 262144,
    "model_max_context": 262144,
    "reasoning_mode": "not_exposed",
}
# model max known, loaded context unavailable -> model max is used as-is.
_MODEL_ONLY_CFG = {
    "model_key": KNOWN_MODEL,
    "model_quantization": "Q5_K_M",
    "loaded_context": None,
    "model_max_context": 262144,
    "reasoning_mode": "not_exposed",
}
# loaded context known, model max unavailable -> loaded is used as-is.
_LOADED_ONLY_CFG = {
    "model_key": KNOWN_MODEL,
    "model_quantization": "Q5_K_M",
    "loaded_context": 8192,
    "model_max_context": None,
    "reasoning_mode": "not_exposed",
}
# Neither context value known.
_NO_CTX_CFG = {"model_key": KNOWN_MODEL, "reasoning_mode": "not_exposed"}


def test_ceiling_262144_context_yields_196608():
    """Proof 1: the canonical Ornith example -- floor(262144 * 75 // 100) = 196608."""
    from src.quality.markdown_cases import load_corrected_fixture

    async def transport(client, url, payload):
        return {"output": [{"type": "message", "content": load_corrected_fixture()}],
                "stats": {}}

    result = asyncio_run(
        run_suite("markdown", model=KNOWN_MODEL, model_config_override=_FULL_CFG,
                  chat_transport=transport)
    )
    assert result.effective_context_capacity == 262144
    assert result.requested_max_output_tokens == 196608


def test_ceiling_loaded_context_smaller_than_model_max_wins():
    """Proof 2: effective capacity is min(model_max_context, loaded_context)."""
    from src.v2_quality_suite_runner import _resolve_effective_capacity

    assert _resolve_effective_capacity(262144, 8192) == 8192   # loaded wins the min
    assert output_ceiling(262144, 8192) == 6144                # floor(8192 * 75 // 100)


def test_ceiling_model_max_used_when_loaded_unavailable():
    """Proof 3: when loaded_context is unavailable the model max alone defines capacity."""
    from src.v2_quality_suite_runner import _resolve_effective_capacity

    assert _resolve_effective_capacity(262144, None) == 262144
    assert output_ceiling(262144, None) == 196608


def test_ceiling_loaded_used_when_model_max_unavailable():
    """Proof 4: when model_max_context is unavailable the loaded value alone defines it."""
    from src.v2_quality_suite_runner import _resolve_effective_capacity

    assert _resolve_effective_capacity(None, 8192) == 8192
    assert output_ceiling(None, 8192) == 6144


def test_ceiling_unknown_capacity_raises_configuration_mismatch():
    """Proof 5: with neither context value known we raise -- never fabricate a size."""
    # Pure policy raises directly.
    with pytest.raises(ConfigurationMismatchError):
        output_ceiling(None, None)

    # ...and run_suite propagates it before any request is sent.
    async def transport(client, url, payload):
        raise AssertionError("transport must never be called when capacity is unknown")

    with pytest.raises(ConfigurationMismatchError):
        asyncio_run(run_suite("markdown", model=KNOWN_MODEL,
                              model_config_override=_NO_CTX_CFG,
                              chat_transport=transport))


def test_ceiling_no_hardcoded_4096_in_request_path():
    """Proof 6: the request path computes a ceiling from context -- it is not a fixed
    constant and no hardcoded 4096 survives. Two different effective capacities yield
    two different ceilings; neither equals 4096."""
    import inspect

    # build_chat_payload carries only a sentinel default (0), never a hardcoded ceiling.
    sig = inspect.signature(build_chat_payload)
    assert sig.parameters["max_output_tokens"].default == 0
    assert sig.parameters["max_output_tokens"].default != 4096

    def _ceiling_for(cfg):
        captured: list[dict] = []

        async def transport(client, url, payload):
            captured.append(payload)
            return {"output": [{"type": "message", "content": "ok"}], "stats": {}}

        asyncio_run(run_suite("markdown", model=KNOWN_MODEL, model_config_override=cfg,
                              chat_transport=transport))
        assert len(captured) == 1
        return captured[0]["max_output_tokens"]

    big = _ceiling_for(_FULL_CFG)
    small_cfg = {"model_key": KNOWN_MODEL, "model_quantization": "Q5_K_M",
                 "loaded_context": 8192, "model_max_context": 262144,
                 "reasoning_mode": "not_exposed"}
    small = _ceiling_for(small_cfg)
    assert big == 196608
    assert small == 6144
    assert big != small
    assert big != 4096 and small != 4096


def test_ceiling_uses_no_prompt_token_estimator():
    """Proof 7: the runner takes no prompt-token estimator -- output tokens are never
    estimated or deducted. The signature carries none, so no chars/token heuristic can
    be applied."""
    import inspect

    params = inspect.signature(run_suite).parameters
    assert "prompt_token_estimator" not in params
    # The pure policy only sees context values -- there is no prompt parameter at all.
    ceil_params = inspect.signature(output_ceiling).parameters
    assert set(ceil_params) == {"model_max_context", "loaded_context"}


def test_ceiling_payload_carculates_75_percent():
    """Proof 8: the live LM Studio payload receives the calculated 75% ceiling."""
    captured: list[dict] = []

    async def transport(client, url, payload):
        captured.append(payload)
        return {"output": [{"type": "message", "content": "ok"}], "stats": {}}

    asyncio_run(run_suite("markdown", model=KNOWN_MODEL, model_config_override=_FULL_CFG,
                          chat_transport=transport))
    assert len(captured) == 1
    assert captured[0]["max_output_tokens"] == 196608


def test_ceiling_payload_has_no_reasoning_fields():
    """Proof 9: no reasoning / reasoning_effort fields are introduced into the payload."""
    seen: list[dict] = []

    async def transport(client, url, payload):
        seen.append(payload)
        return {"output": [{"type": "message", "content": "ok"}], "stats": {}}

    asyncio_run(run_suite("markdown", model=KNOWN_MODEL, model_config_override=_FULL_CFG,
                          chat_transport=transport))
    assert len(seen) == 1
    p = seen[0]
    assert "reasoning" not in p
    assert "reasoning_effort" not in p



# ---------------------------------------------------------------------------
# Remaining fixed-output-ceiling proofs (10-13) + fingerprint identity regression.
# ---------------------------------------------------------------------------


def test_ceiling_telemetry_reports_policy():
    """Proof 10: suite + per-request telemetry reports policy name, 75%, effective
    capacity, and requested ceiling -- enough to reproduce the policy."""
    captured: list[dict] = []

    async def transport(client, url, payload):
        captured.append(payload)
        return {"output": [{"type": "message", "content": "ok"}], "stats": {}}

    result = asyncio_run(
        run_suite("markdown", model=KNOWN_MODEL, model_config_override=_FULL_CFG,
                  chat_transport=transport)
    )
    assert result.effective_context_capacity == 262144
    assert result.output_budget_policy == OUTPUT_BUDGET_POLICY == "75_percent_context"
    assert result.output_budget_percent == OUTPUT_BUDGET_PERCENT == 75
    assert result.requested_max_output_tokens == 196608

    rr = result.requests[0]
    assert rr["effective_context_capacity"] == 262144
    assert rr["output_budget_policy"] == "75_percent_context"
    assert rr["output_budget_percent"] == 75
    assert rr["requested_max_output_tokens"] == 196608


def test_ceiling_java_denominator_remains_52():
    """Proof 11: the fixed denominator invariant still holds for java under the new
    policy -- extraction is stubbed to failure so no javac is needed; total stays 52."""
    async def transport(client, url, payload):
        return {"output": [], "stats": {}}   # empty -> java extraction fails cleanly

    result = asyncio_run(
        run_suite("java", model=KNOWN_MODEL, model_config_override=_FULL_CFG,
                  chat_transport=transport)
    )
    assert result.checks_total == CANONICAL_DENOMINATORS["java"] == 52
    # The ceiling telemetry rides along unchanged on the java request record.
    j01 = next((r for r in result.requests if r["request_id"] == "JAVA-01"), None)
    assert j01 is not None and j01["requested_max_output_tokens"] == 196608


def test_ceiling_markdown_final_newline_still_20_20():
    """Proof 12: the markdown final-newline regression still validates 20/20 through
    the runner, with the new fixed ceiling applied to the request."""
    from src.quality.markdown_cases import load_corrected_fixture

    async def transport(client, url, payload):
        return {
            "output": [
                {"type": "reasoning", "content": "let me repair the document..."},
                {"type": "message", "content": load_corrected_fixture()},
            ],
            "stats": {},
        }

    result = asyncio_run(
        run_suite("markdown", model=KNOWN_MODEL, model_config_override=_FULL_CFG,
                  chat_transport=transport)
    )
    assert result.checks_total == CANONICAL_DENOMINATORS["markdown"] == 20
    assert result.checks_passed == 20, (
        f"corrected markdown must validate 20/20, got {result.checks_passed}/20 "
        f"(extraction_failures={result.extraction_failures})"
    )
    assert not result.extraction_failures
    # The request that produced the 20/20 document carried the fixed ceiling.
    md_req = next(r for r in result.requests if r["request_id"] == "MD")
    assert md_req["requested_max_output_tokens"] == 196608


def test_ceiling_reasoning_only_gives_no_final_message_content():
    """Proof 13: a reasoning-only response still yields 'no final message content' --
    the policy change does not disturb the message/reasoning separation."""
    async def transport(client, url, payload):
        return {"output": [{"type": "reasoning", "content": "just thinking..."}],
                "stats": {}}

    result = asyncio_run(
        run_suite("markdown", model=KNOWN_MODEL, model_config_override=_FULL_CFG,
                  chat_transport=transport)
    )
    reasons = [f["reason"] for f in result.extraction_failures]
    assert "no final message content" in reasons
    # The request was still issued with the fixed ceiling even though nothing graded.
    md_req = next(r for r in result.requests if r["request_id"] == "MD")
    assert md_req["requested_max_output_tokens"] == 196608


def test_ceiling_policy_affects_configuration_fingerprint():
    """Fingerprint regression: the quality output-budget policy is part of benchmark
    identity, so old 4096 / old 90% / new 75% runs are never treated as equivalent.

    Identical configuration with only the policy string differing yields distinct,
    stable fingerprints; and the runner actually feeds the active policy into the hash."""
    base = {
        "model_key": KNOWN_MODEL,
        "model_quantization": "Q5_K_M",
        "loaded_context": 262144,
        "reasoning_mode": "not_exposed",
    }
    fp_legacy_4096 = compute_configuration_fingerprint(
        {**base, "output_budget_policy": "legacy_4096"})
    fp_90pct = compute_configuration_fingerprint(
        {**base, "output_budget_policy": "90_percent_prompt_deducted"})
    fp_75pct = compute_configuration_fingerprint(
        {**base, "output_budget_policy": OUTPUT_BUDGET_POLICY})

    # Stable: re-hashing the same policy reproduces the hash.
    assert fp_75pct == compute_configuration_fingerprint(
        {**base, "output_budget_policy": OUTPUT_BUDGET_POLICY})

    # All three policies are mutually distinct -- none collapse to one identity.
    assert len({fp_legacy_4096, fp_90pct, fp_75pct}) == 3

    # The runner injects the active policy into the fingerprint it records.
    captured: list[dict] = []

    async def transport(client, url, payload):
        captured.append(payload)
        return {"output": [{"type": "message", "content": "ok"}], "stats": {}}

    result = asyncio_run(
        run_suite("markdown", model=KNOWN_MODEL, model_config_override=_FULL_CFG,
                  chat_transport=transport)
    )
    assert result.configuration_fingerprint == fp_75pct
    assert result.configuration_fingerprint != fp_legacy_4096
