"""HTTP route tests for src/routes/v2_results.py (UI Act 4).

Exercises the real persistence -> read-model -> HTTP path against a throwaway
artifact directory (no LM Studio, no live execution). Covers status mapping,
authoritative pass-through, canonical denominators, config/text/telemetry
preservation, integrity/schema error handling and legacy-executor isolation.
"""

from __future__ import annotations

import inspect
import json
import re

import pytest
from fastapi.testclient import TestClient

import src.main
import src.v2_quality_artifact as art
from src.v2_quality_artifact import (
    ARTIFACT_TYPE,
    SCHEMA_VERSION,
    persist_run,
)
from src.v2_quality_suite_runner import (
    CANONICAL_DENOMINATORS,
    SUITES,
    TOTAL_DENOMINATOR,
    SuiteResult,
    combine_suite_results,
)

client = TestClient(src.main.app)

RUN_ID = "run-272c02a4c305"
MODEL = "ornith-1.5-35b-a3b"
FINGERPRINT = (
    "010aa87e7693e968fe7f7d9556ba71e174d374a3026364d71586067d8757e88b"
)


# ---------------------------------------------------------------------------
# Helpers: build representative, internally-consistent artifacts on disk.
# ---------------------------------------------------------------------------

def _suite_result(suite, passed, *, requests=None, validation_failures=None,
                  extraction_failures=None):
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
    if suite == "python":
        base.update(ttft_seconds=1.5, prefill_throughput=8000.0,
                    decode_throughput=1500.0, reasoning_tokens=42)
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
    return [
        _suite_result("python", 55,
                      requests=[{
                          "request_id": "R-PY-1",
                          "extraction_success": True,
                          "checks_passed": 55,
                          "checks_total_validator": 9999,   # misleading diagnostic total
                          "ttft_seconds": 1.5,
                          "prefill_throughput": 8000.0,
                          "decode_throughput": 1500.0,
                          "reasoning_tokens": 42,
                      }],
                      validation_failures=[{
                          "case_id": "PY-CASE-1", "name": "foo_asserts",
                          "failure_type": "assertion", "failure_reason": "expected 1 got 2",
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
                          "case_id": "MD-CASE-7", "name": "heading_level",
                          "failure_type": "markdownlint", "failure_reason": messy,
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


@pytest.fixture()
def v2_runs_dir(tmp_path, monkeypatch):
    """Point the artifact store at a throwaway directory for the duration of a test."""
    monkeypatch.setattr(art, "V2_RUNS_DIR", tmp_path)
    return tmp_path


def _persist_valid(v2_runs_dir):
    suites = _representative_suites()
    aggregate = combine_suite_results(suites)
    persist_run(aggregate, suites)
    return RUN_ID


# ---------------------------------------------------------------------------
# 1. Known run -> 200 (full real path: disk -> artifact.load -> read model)
# ---------------------------------------------------------------------------

def test_known_run_returns_200_with_all_sections(v2_runs_dir):
    _persist_valid(v2_runs_dir)

    resp = client.get(f"/api/v2/results/{RUN_ID}")
    assert resp.status_code == 200

    body = resp.json()
    for key in ("run", "suites", "configuration", "failures", "successes", "telemetry"):
        assert key in body

    run = body["run"]
    assert run["run_id"] == RUN_ID
    assert run["model_identifier"] == MODEL
    assert run["checks_passed"] == 148
    assert run["checks_total"] == TOTAL_DENOMINATOR == 166

    # canonical suite order + denominators preserved verbatim.
    assert [s["suite"] for s in body["suites"]] == list(SUITES)
    denom = {s["suite"]: s["checks_total"] for s in body["suites"]}
    assert denom == {"python": 58, "java": 52, "markdown": 20, "evidence": 30, "drift": 6}


# ---------------------------------------------------------------------------
# 2. Unknown run -> 404 (no generic 500)
# ---------------------------------------------------------------------------

def test_unknown_run_returns_404(v2_runs_dir):
    resp = client.get("/api/v2/results/run-does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert "not found" in body["detail"].lower()


# ---------------------------------------------------------------------------
# 3. Canonical values pass through unchanged (no row-derived aggregate)
# ---------------------------------------------------------------------------

def test_canonical_aggregate_passthrough_unaffected_by_misleading_rows(v2_runs_dir):
    _persist_valid(v2_runs_dir)   # python request carries checks_total_validator=9999

    body = client.get(f"/api/v2/results/{RUN_ID}").json()

    # Authoritative aggregate is untouched by the bogus diagnostic total.
    assert body["run"]["checks_passed"] == 148
    assert body["run"]["checks_total"] == 166
    python = next(s for s in body["suites"] if s["suite"] == "python")
    assert (python["checks_passed"], python["checks_total"]) == (55, 58)

    # The diagnostic row is still surfaced verbatim as inspection detail ...
    py_succ = next(s for s in body["successes"] if s["request_id"] == "R-PY-1")
    assert py_succ["checks_total"] == 9999
    # ... but it never becomes the canonical score (which stays /58, not /9999).
    assert body["suites"][0]["checks_total"] == 58


# ---------------------------------------------------------------------------
# 4. Canonical suite denominators (explicit)
# ---------------------------------------------------------------------------

def test_canonical_suite_denominators(v2_runs_dir):
    _persist_valid(v2_runs_dir)
    body = client.get(f"/api/v2/results/{RUN_ID}").json()
    denom = {s["suite"]: s["checks_total"] for s in body["suites"]}
    assert denom == CANONICAL_DENOMINATORS


# ---------------------------------------------------------------------------
# 5. Config preservation
# ---------------------------------------------------------------------------

def test_configuration_preserved(v2_runs_dir):
    _persist_valid(v2_runs_dir)
    cfg = client.get(f"/api/v2/results/{RUN_ID}").json()["configuration"]

    assert cfg["configuration_fingerprint"] == FINGERPRINT          # full fingerprint
    assert cfg["effective_context_capacity"] == 262144
    assert cfg["output_budget_policy"] == "75_percent_context"
    assert cfg["requested_max_output_tokens"] == 196608
    assert cfg["reasoning_policy"] == "inherit"


def test_run_level_classification_preserved(v2_runs_dir):
    _persist_valid(v2_runs_dir)
    run = client.get(f"/api/v2/results/{RUN_ID}").json()["run"]
    assert run["classification"] == "incomplete"


# ---------------------------------------------------------------------------
# 6. Exact text survives API serialization/deserialization
# ---------------------------------------------------------------------------

def test_exact_evidence_text_survives_api_round_trip(v2_runs_dir):
    _persist_valid(v2_runs_dir)
    body = client.get(f"/api/v2/results/{RUN_ID}").json()

    messy = "  \n\tindent\n\n\nmultiple\nblank\nlines\ttrailing \n"
    md_failure = next(f for f in body["failures"] if f["suite"] == "markdown")
    assert md_failure["failure_reason"] == messy


# ---------------------------------------------------------------------------
# 7. Null telemetry preserved (never coerced to 0)
# ---------------------------------------------------------------------------

def test_null_telemetry_preserved_over_api(v2_runs_dir):
    _persist_valid(v2_runs_dir)
    body = client.get(f"/api/v2/results/{RUN_ID}").json()

    telem = {t["suite"]: t for t in body["telemetry"]["suites"]}
    # python has real telemetry -> preserved.
    assert telem["python"]["ttft_seconds"] == 1.5
    assert telem["python"]["reasoning_tokens"] == 42
    # java/evidence/etc. have no telemetry overrides -> JSON null, not 0.
    assert telem["java"]["ttft_seconds"] is None
    assert telem["java"]["prefill_throughput"] is None
    assert telem["evidence"]["decode_throughput"] is None
    assert telem["drift"]["ttft_seconds"] is None


# ---------------------------------------------------------------------------
# 8. Integrity error -> explicit server error (no repair)
# ---------------------------------------------------------------------------

def test_integrity_error_maps_to_500(v2_runs_dir):
    _persist_valid(v2_runs_dir)

    # Corrupt the on-disk artifact: make an aggregate per-suite denominator
    # disagree with the canonical value. The read model must refuse, not repair.
    path = art.artifact_path(RUN_ID)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["aggregate"]["per_suite"]["python"] = {"passed": 55, "total": 59}  # wrong denom
    path.write_text(json.dumps(doc), encoding="utf-8")

    resp = client.get(f"/api/v2/results/{RUN_ID}")
    assert resp.status_code == 500
    body = resp.json()
    assert body.get("detail")                           # concise, not a stack trace


# ---------------------------------------------------------------------------
# 9. Unsupported schema -> explicit non-404 error
# ---------------------------------------------------------------------------

def test_unsupported_schema_maps_to_422(v2_runs_dir):
    # A persisted artifact whose format/version this build cannot process.
    path = art.artifact_path(RUN_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": SCHEMA_VERSION + 1,
        "artifact_type": ARTIFACT_TYPE,
        "run_id": RUN_ID,
    }), encoding="utf-8")

    resp = client.get(f"/api/v2/results/{RUN_ID}")
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"] and "not found" not in body["detail"].lower()


# ---------------------------------------------------------------------------
# 10. Legacy executor isolation (import-boundary + behavioural)
# ---------------------------------------------------------------------------

def test_route_does_not_import_or_call_legacy_executor():
    import src.routes.v2_results as v2r

    source = inspect.getsource(v2r)
    assert not re.search(r"\b(import|from)\b[^\n]*v2_quality_executor", source), \
        "route must not import the legacy executor"
    assert ".run_v2_quality_live(" not in source, \
        "route must not call run_v2_quality_live"


def test_route_does_not_invoke_run_v2_quality_live_behaviourally(v2_runs_dir):
    _persist_valid(v2_runs_dir)

    import src.v2_quality_executor as legacy

    def _boom(*_a, **_k):
        raise AssertionError("run_v2_quality_live must not be called by the API route")

    original = legacy.run_v2_quality_live
    legacy.run_v2_quality_live = _boom
    try:
        resp = client.get(f"/api/v2/results/{RUN_ID}")
        assert resp.status_code == 200
        assert resp.json()["run"]["checks_total"] == 166
    finally:
        legacy.run_v2_quality_live = original


# ---------------------------------------------------------------------------
# run-id safety: unsafe/malformed ids are rejected by the loader, mapped to HTTP.
# ---------------------------------------------------------------------------

def test_pathlike_run_id_is_rejected(v2_runs_dir):
    # Backslash is a path separator on Windows and must never reach the filesystem.
    resp = client.get("/api/v2/results/foo\\bar")
    assert resp.status_code == 400


def test_empty_run_id_returns_400_or_404(v2_runs_dir):
    # Empty id fails the loader's non-empty guard (ValueError) -> 400, never a read.
    resp = client.get("/api/v2/results/")
    # Starlette collapses a trailing-slash empty segment to the parent route; either
    # a 400 from our handler or a 404 is acceptable -- crucially not a 200 and never
    # an arbitrary filesystem read.
    assert resp.status_code in (400, 404)
