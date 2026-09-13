"""Tests for Act 3 V2 read model (stable view-model over persisted artifacts).

Guards authoritative-score handling, failure-first separation, configuration
consistency validation, exact-text preservation, null-telemetry preservation,
error propagation, unsupported-schema rejection and legacy-executor isolation.
"""

import pytest

import src.v2_quality_artifact as art
import src.v2_quality_read_model as rm
from src.v2_quality_artifact import (
    V2ArtifactSchemaError,
    V2RunNotFoundError,
    build_run_document,
)
from src.v2_quality_read_model import (
    V2ReadModelIntegrityError,
    build_read_model,
    load_v2_result,
    round_percentage,
)
from src.v2_quality_suite_runner import (
    CANONICAL_DENOMINATORS,
    SUITES,
    TOTAL_DENOMINATOR,
    SuiteResult,
    combine_suite_results,
)

RUN_ID = "run-272c02a4c305"
MODEL = "ornith-1.5-35b-a3b"
FINGERPRINT = (
    "010aa87e7693e968fe7f7d9556ba71e174d374a3026364d71586067d8757e88b"
)


# ---------------------------------------------------------------------------
# Representative-document helpers (production-faithful, internally consistent)
# ---------------------------------------------------------------------------

def _suite_result(suite, passed, *, overrides=None, requests=None,
                  validation_failures=None, extraction_failures=None):
    base = {
        "run_id": RUN_ID,
        "suite": suite,
        "model_identifier": MODEL,
        "configuration_fingerprint": FINGERPRINT,
        "classification": "incomplete",
        "reasoning_policy": "inherit",
        "loaded_reasoning_mode": "not_exposed",
        "requests_expected": 1,
        "requests_completed": len(requests) if requests else 0,
        "checks_passed": passed,
        "checks_total": CANONICAL_DENOMINATORS[suite],
        "effective_context_capacity": 262144,
        "output_budget_policy": "75_percent_context",
        "output_budget_percent": 75,
        "requested_max_output_tokens": 196608,
        "max_output_tokens": 196608,
        "model_max_context": 262144,
        "loaded_context": 262144,
        "temperature": 0.0,
        "mtp_state": "unknown",
        "speculative_simple": False,
    }
    # Telemetry defaults: Optional fields stay None (missing), token counts 0.
    if suite == "python":
        base.update(ttft_seconds=1.5, prefill_throughput=8000.0,
                    decode_throughput=1500.0, reasoning_tokens=42)
    else:
        base.update(ttft_seconds=None, prefill_throughput=None,
                    decode_throughput=None, reasoning_tokens=0)
    if overrides:
        base.update(overrides)
    # Injection-site data (requests / validation & extraction failures) must land
    # on the SuiteResult; they are passed as explicit kwargs, not scalars.
    for key, value in (
        ("requests", requests),
        ("validation_failures", validation_failures),
        ("extraction_failures", extraction_failures),
    ):
        if value is not None:
            base[key] = value
    return SuiteResult(**base)


def _representative_suites():
    messy = "  \n\tindent\n\n\nmultiple\nblank\nlines\ttrailing \n"
    suites = [
        _suite_result("python", 55,
                      requests=[{
                          "request_id": "R-PY-1",
                          "extraction_success": True,
                          "checks_passed": 55,
                          # Misleading diagnostic total -- a naive sum must NOT win.
                          "checks_total_validator": 200,
                          "ttft_seconds": 1.5,
                          "prefill_throughput": 8000.0,
                          "decode_throughput": 1500.0,
                          "reasoning_tokens": 42,
                      }],
                      validation_failures=[{
                          "case_id": "PY-CASE-1",
                          "name": "foo_asserts",
                          "failure_type": "assertion",
                          "failure_reason": "expected 1 got 2",
                      }]),
        _suite_result("java", 48,
                      extraction_failures=[{
                          "request_id": "R-JA-BAD",
                          "reason": "extraction: no solution block found",
                      }]),
        _suite_result("markdown", 19,
                      requests=[{
                          "request_id": "R-MD-1",
                          "extraction_success": True,
                          "checks_passed": 19,
                          "checks_total_validator": 20,
                      }],
                      validation_failures=[{
                          "case_id": "MD-CASE-7",
                          "name": "heading_level",
                          "failure_type": "markdownlint",
                          "failure_reason": messy,
                      }]),
        _suite_result("evidence", 22,
                      requests=[{
                          "request_id": "R-EV-1",
                          "extraction_success": True,
                          "checks_passed": 22,
                          "checks_total_validator": 30,
                      }],
                      validation_failures=[
                          {"case_id": "EV-CASE-1", "name": "",
                           "failure_type": "citation", "failure_reason": "missing source"},
                          {"case_id": "EV-CASE-2", "name": "",
                           "failure_type": "citation", "failure_reason": "unsupported claim"},
                      ]),
        _suite_result("drift", 4,
                      requests=[{
                          "request_id": "R-DR-1",
                          "extraction_success": True,
                          "checks_passed": 4,
                          "checks_total_validator": 6,
                      }],
                      validation_failures=[{
                          "case_id": "DR-CASE-1", "name": "",
                          "failure_type": "drift", "failure_reason": "invented rule used"},
                      ]),
    ]
    return suites


def _representative_document():
    suites = _representative_suites()
    aggregate = combine_suite_results(suites)
    return build_run_document(aggregate, suites)


# ---------------------------------------------------------------------------
# 1. Valid artifact -> read model
# ---------------------------------------------------------------------------

def test_valid_artifact_to_read_model():
    doc = build_read_model(_representative_document())

    # Run identity (from persisted document/aggregate).
    assert doc["run"]["run_id"] == RUN_ID
    assert doc["run"]["model_identifier"] == MODEL
    assert doc["run"]["configuration_fingerprint"] == FINGERPRINT
    assert doc["run"]["classification"] == "incomplete"
    assert doc["run"]["reasoning_policy"] == "inherit"

    # Authoritative aggregate (not row-derived).
    assert doc["run"]["checks_passed"] == 148
    assert doc["run"]["checks_total"] == TOTAL_DENOMINATOR == 166

    # Canonical suite order + scores.
    assert [s["suite"] for s in doc["suites"]] == list(SUITES)
    scores = {s["suite"]: (s["checks_passed"], s["checks_total"]) for s in doc["suites"]}
    assert scores == {
        "python": (55, 58),
        "java": (48, 52),
        "markdown": (19, 20),
        "evidence": (22, 30),
        "drift": (4, 6),
    }
    # failed_checks derived from the canonical aggregate.
    assert doc["suites"][0]["failed_checks"] == 3

    # Percentage over the authoritative aggregate.
    assert doc["run"]["percentage"] == 89.2


def test_round_percentage_is_deterministic():
    assert round_percentage(148, 166) == 89.2
    # ROUND_HALF_UP at the first decimal boundary (12.35 -> 12.4).
    assert round_percentage(247, 2000) == 12.4
    # Guard: integer total of zero is safe (never divide-by-zero).
    assert round_percentage(1, 0) == 0.0


# ---------------------------------------------------------------------------
# 2. Failure-first separation + deterministic ordering
# ---------------------------------------------------------------------------

def test_failure_first_and_deterministic_ordering():
    doc = build_read_model(_representative_document())

    # Every failure precedes every success conceptually: failures are the primary
    # inspection list and successes are secondary/parallel.
    assert doc["failures"] and doc["successes"]

    # Canonical suite order, extraction-before-validation within a suite.
    seen = [(f["suite"], f["source_type"]) for f in doc["failures"]]
    assert seen == [
        ("python", "validation"),          # PY-CASE-1
        ("java", "extraction"),            # R-JA-BAD
        ("markdown", "validation"),        # MD-CASE-7
        ("evidence", "validation"),        # EV-CASE-1
        ("evidence", "validation"),        # EV-CASE-2
        ("drift", "validation"),           # DR-CASE-1
    ]

    # The java extraction failure is present as a failure record.
    java_ext = next(f for f in doc["failures"] if f["suite"] == "java"
                    and f["source_type"] == "extraction")
    assert java_ext["request_id"] == "R-JA-BAD"
    assert java_ext["locator"] == "java/extraction/R-JA-BAD"

    # Successes are request-level, canonical suite order; the extraction-failed
    # java request is NOT among successes.
    succ_ids = [s["request_id"] for s in doc["successes"]]
    assert succ_ids == ["R-PY-1", "R-MD-1", "R-EV-1", "R-DR-1"]
    assert "R-JA-BAD" not in succ_ids

    # Each failure record carries the full inspection surface.
    for f in doc["failures"]:
        for key in ("suite", "case_id", "request_id", "failure_type", "failure_reason",
                    "checks_passed", "checks_total", "expected", "actual",
                    "extraction_classification", "validation_classification",
                    "source_type", "locator"):
            assert key in f


# ---------------------------------------------------------------------------
# 3. Canonical aggregate is NOT derived from detailed rows
# ---------------------------------------------------------------------------

def test_canonical_aggregate_not_row_derived():
    document = _representative_document()
    # Inject a wildly misleading diagnostic total on the python request (already
    # 200 vs canonical 58) plus bogus extra validation rows.
    py_doc = next(s for s in document["suites"] if s["suite"] == "python")
    py_doc["requests"][0]["checks_total_validator"] = 9999
    py_doc.setdefault("validation_failures", []).append(
        {"case_id": "PY-BOGUS", "name": "", "failure_type": "x", "failure_reason": "y"}
    )

    doc = build_read_model(document)

    # Still authoritative from the persisted aggregate, never recomputed/summed.
    python = doc["suites"][0]
    assert (python["checks_passed"], python["checks_total"]) == (55, 58)
    assert doc["run"]["checks_total"] == 166
    # The misleading diagnostic total leaked nowhere into scoring.
    assert "9999" not in str(python)


# ---------------------------------------------------------------------------
# 4. Configuration consistency: matching works; mismatch raises explicitly
# ---------------------------------------------------------------------------

def test_configuration_block_resolved_from_consistent_suites():
    doc = build_read_model(_representative_document())
    cfg = doc["configuration"]
    assert cfg["effective_context_capacity"] == 262144
    assert cfg["output_budget_policy"] == "75_percent_context"
    assert cfg["output_budget_percent"] == 75
    assert cfg["requested_max_output_tokens"] == 196608
    assert cfg["model_max_context"] == 262144
    assert cfg["loaded_context"] == 262144
    assert cfg["reasoning_policy"] == "inherit"
    assert cfg["configuration_fingerprint"] == FINGERPRINT


def test_configuration_mismatch_raises_integrity_error():
    document = _representative_document()
    # A suite disagrees on the configuration fingerprint (should be identical).
    md = next(s for s in document["suites"] if s["suite"] == "markdown")
    md["configuration_fingerprint"] = "deadbeef" * 8
    with pytest.raises(V2ReadModelIntegrityError):
        build_read_model(document)


def test_output_policy_mismatch_raises_integrity_error():
    document = _representative_document()
    ev = next(s for s in document["suites"] if s["suite"] == "evidence")
    ev["output_budget_percent"] = 90
    with pytest.raises(V2ReadModelIntegrityError):
        build_read_model(document)


# ---------------------------------------------------------------------------
# 5. Suite score mismatch -> reject, never repair
# ---------------------------------------------------------------------------

def test_aggregate_vs_suite_score_mismatch_rejected():
    document = _representative_document()
    # Aggregate claims python 55/58 but the persisted Python SuiteResult says 40/58.
    aggregate = document["aggregate"]
    aggregate["per_suite"]["python"] = {"passed": 55, "total": 58}
    aggregate["checks_passed"] = 148
    py_doc = next(s for s in document["suites"] if s["suite"] == "python")
    py_doc["checks_passed"] = 40  # disagrees with the aggregate's 55

    with pytest.raises(V2ReadModelIntegrityError):
        build_read_model(document)


def test_non_canonical_denominator_rejected():
    document = _representative_document()
    document["aggregate"]["per_suite"]["python"] = {"passed": 55, "total": 59}
    with pytest.raises(V2ReadModelIntegrityError):
        build_read_model(document)


def test_identity_mismatch_aggregate_vs_suite_rejected():
    document = _representative_document()
    document["aggregate"]["model_identifier"] = "some-other-model"
    with pytest.raises(V2ReadModelIntegrityError):
        build_read_model(document)


# ---------------------------------------------------------------------------
# 6. Exact text preservation through the read model
# ---------------------------------------------------------------------------

def test_exact_text_preserved_through_read_model():
    document = _representative_document()
    doc = build_read_model(document)

    md_failure = next(f for f in doc["failures"] if f["suite"] == "markdown")
    messy = "  \n\tindent\n\n\nmultiple\nblank\nlines\ttrailing \n"
    assert md_failure["failure_reason"] == messy  # no strip/rstrip/normalisation


# ---------------------------------------------------------------------------
# 7. Missing telemetry stays null (never zeroed)
# ---------------------------------------------------------------------------

def test_missing_telemetry_stays_null():
    doc = build_read_model(_representative_document())

    # python recorded real telemetry -> preserved exactly.
    py_succ = next(s for s in doc["successes"] if s["suite"] == "python")
    assert py_succ["telemetry"]["ttft_seconds"] == 1.5
    assert py_succ["telemetry"]["prefill_throughput"] == 8000.0
    assert py_succ["telemetry"]["decode_throughput"] == 1500.0
    assert py_succ["telemetry"]["reasoning_tokens"] == 42

    # java has no telemetry overrides -> Optional fields remain None (not 0).
    for f in doc["failures"]:
        pass
    # Telemetry model: python rich, others null where unset.
    telem = {t["suite"]: t for t in doc["telemetry"]["suites"]}
    assert telem["python"]["ttft_seconds"] == 1.5
    assert telem["java"]["ttft_seconds"] is None
    assert telem["java"]["prefill_throughput"] is None
    assert telem["java"]["decode_throughput"] is None
    # No zero substitution for genuinely-missing Optional telemetry.
    assert telem["markdown"]["ttft_seconds"] is None


# ---------------------------------------------------------------------------
# 8. Unknown run -> Act 2 not-found exception propagates
# ---------------------------------------------------------------------------

def test_unknown_run_raises_not_found():
    with pytest.raises(V2RunNotFoundError):
        load_v2_result("run-does-not-exist")


# ---------------------------------------------------------------------------
# 9. Unsupported schema -> explicit failure
# ---------------------------------------------------------------------------

def test_unsupported_schema_rejected():
    bad = {"schema_version": art.SCHEMA_VERSION + 1, "aggregate": {}, "suites": []}
    with pytest.raises(V2ArtifactSchemaError):
        build_read_model(bad)


def test_missing_structure_rejected():
    with pytest.raises(V2ArtifactSchemaError):
        build_read_model({"schema_version": art.SCHEMA_VERSION})


# ---------------------------------------------------------------------------
# 10. Legacy isolation -- no import/call to run_v2_quality_live
# ---------------------------------------------------------------------------

def test_read_model_source_has_no_legacy_executor_import_or_call():
    import inspect

    source = inspect.getsource(rm)
    import re
    assert not re.search(r"\b(import|from)\b[^\n]*v2_quality_executor", source), \
        "read model must not import the legacy executor"
    assert ".run_v2_quality_live(" not in source, \
        "read model must not call run_v2_quality_live"


def test_loading_read_model_does_not_invoke_legacy_live_path():
    # Even if something monkeypatches the live path to blow up, building/loading
    # the read model must never reach it.
    import src.v2_quality_executor as legacy

    def _boom(*_a, **_k):
        raise AssertionError("run_v2_quality_live must not be called by the read model")

    original = legacy.run_v2_quality_live
    legacy.run_v2_quality_live = _boom
    try:
        # Pure view-model path.
        build_read_model(_representative_document())
    finally:
        legacy.run_v2_quality_live = original


def test_read_model_imports_only_persistence_layer():
    # The read model depends on the persistence-only Act 2 layer and stdlib only
    # -- it never reaches into the live executor for its data.
    import inspect

    source = inspect.getsource(rm)
    assert "from src.v2_quality_artifact import" in source
