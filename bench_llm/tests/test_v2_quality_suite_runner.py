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
    run_suite,
)
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
    passed, total = account_checks("java", rows, collapse=False)
    assert total == CANONICAL_DENOMINATORS["java"] == 52          # never inflated
    assert passed < 52                                             # that case's checks failed
    assert passed == 0


# ---------------------------------------------------------------------------
# Proof 3: Successful reference results -> 58/52/20/30/6, total 166.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("suite", SUITES)
def test_accounting_maps_perfect_result_onto_canonical_denominator(suite):
    passed, total = account_checks(suite, _perfect_units(suite), collapse=False)
    assert (passed, total) == (CANONICAL_DENOMINATORS[suite], CANONICAL_DENOMINATORS[suite])


def test_accounting_caps_passed_at_denominator():
    # A validator that emitted more passing units than canonical must not exceed D.
    over = [CaseResult("X", "python", "x", True, 1, 1) for _ in range(58 + 25)]
    passed, total = account_checks("python", over, collapse=False)
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


if __name__ == "__main__":  # pragma: no cover - allows direct execution
    raise SystemExit(pytest.main([__file__, "-v"]))
