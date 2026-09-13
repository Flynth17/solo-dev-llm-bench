"""Tests for Act 2 durable V2 result artifact persistence.

These guard the smallest safe durable-result path for the locked suite runner:
faithful round-trip, canonical aggregate preservation, config/telemetry fidelity,
exact-text evidence, atomic write safety, not-found handling and the hard
prohibition on the legacy executor.
"""

import json

import pytest

from src.v2_quality_artifact import (
    SCHEMA_VERSION,
    ARTIFACT_TYPE,
    V2RunNotFoundError,
    V2ArtifactSchemaError,
    build_run_document,
    persist_run,
    persist_document,
    load,
    try_load,
    artifact_path,
    _atomic_write_json,
)
from src.v2_quality_suite_runner import (
    SuiteResult,
    CANONICAL_DENOMINATORS,
    TOTAL_DENOMINATOR,
    SUITES,
    combine_suite_results,
)

KNOWN_MODEL = "ornith-1.5-35b-a3b"
KNOWN_FINGERPRINT = (
    "010aa87e7693e968fe7f7d9556ba71e174d374a3026364d71586067d8757e88b"
)
KNOWN_RUN_ID = "run-272c02a4c305"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_suite(suite, *, passed, run_id=KNOWN_RUN_ID, fingerprint=KNOWN_FINGERPRINT,
                model=KNOWN_MODEL, classification="incomplete", **overrides):
    base = dict(
        run_id=run_id,
        suite=suite,
        model_identifier=model,
        configuration_fingerprint=fingerprint,
        classification=classification,
        reasoning_policy="inherit",
        loaded_reasoning_mode="not_exposed",
        requests_expected=1,
        requests_completed=1,
        checks_passed=passed,
        checks_total=CANONICAL_DENOMINATORS[suite],
    )
    base.update(overrides)
    return SuiteResult(**base)


@pytest.fixture()
def isolated_runs_dir(tmp_path, monkeypatch):
    """Point the artifact store at a throwaway directory (never touch repo data/)."""
    from src import v2_quality_artifact as art

    target = tmp_path / "v2_runs"
    monkeypatch.setattr(art, "V2_RUNS_DIR", target)
    return target


def _representative_suites():
    """Five canonical suites with a realistic partial pass + rich fields."""
    suites = [
        _make_suite("python", passed=55,
                    effective_context_capacity=262144,
                    requested_max_output_tokens=196608,
                    max_output_tokens=196608,
                    model_max_context=262144,
                    loaded_context=262144,
                    ttft_seconds=1.25,
                    prefill_throughput=8200.0,
                    decode_throughput=1503.5,
                    reasoning_tokens=42,
                    extraction_failures=[],
                    validation_failures=[
                        {"case_id": "py_foo", "name": "foo",
                         "failure_type": "assertion", "failure_reason": "expected 1 got 2"},
                    ],
                    requests=[{
                        "request_id": "req-py-1",
                        "extraction_success": True,
                        "checks_passed": 55,
                        "checks_total_validator": 58,
                    }]),
        _make_suite("java", passed=48),
        _make_suite("markdown", passed=19),
        _make_suite("evidence", passed=22),
        _make_suite("drift", passed=4),
    ]
    return suites


# ---------------------------------------------------------------------------
# 1. Round trip
# ---------------------------------------------------------------------------

def test_round_trip_preserves_authoritative_document(isolated_runs_dir):
    aggregate = combine_suite_results(_representative_suites())
    path = persist_run(aggregate, _representative_suites())

    assert path == artifact_path(KNOWN_RUN_ID)
    doc = load(KNOWN_RUN_ID)

    # Aggregate verbatim.
    assert doc["aggregate"] == aggregate
    # Identity fields lifted from the aggregate.
    assert doc["run_id"] == aggregate["run_id"]
    assert doc["model_identifier"] == aggregate["model_identifier"]
    assert doc["configuration_fingerprint"] == aggregate["configuration_fingerprint"]
    assert doc["classification"] == aggregate["classification"]
    assert doc["reasoning_policy"] == aggregate["reasoning_policy"]
    # Five authoritative suite documents, in canonical order.
    assert [s["suite"] for s in doc["suites"]] == list(SUITES)
    for got, want in zip(doc["suites"], _representative_suites()):
        assert got == want.to_dict()


# ---------------------------------------------------------------------------
# 2. Canonical aggregate preserved
# ---------------------------------------------------------------------------

def test_canonical_aggregate_preserved(isolated_runs_dir):
    suites = _representative_suites()
    aggregate = combine_suite_results(suites)
    persist_run(aggregate, suites)
    doc = load(KNOWN_RUN_ID)

    agg = doc["aggregate"]
    assert agg["checks_total"] == TOTAL_DENOMINATOR == 166
    for suite, denom in CANONICAL_DENOMINATORS.items():
        per_suite = agg["per_suite"][suite]
        assert per_suite["total"] == denom
    # Authoritative "/N" strings preserved (not recomputed by the UI later).
    assert agg["python"] == "55/58"
    assert agg["java"] == "48/52"
    assert agg["markdown"] == "19/20"
    assert agg["evidence"] == "22/30"
    assert agg["drift"] == "4/6"
    # Total is the sum of canonical per-suite passes.
    total_passed = sum(agg["per_suite"][s]["passed"] for s in SUITES)
    assert agg["checks_passed"] == total_passed == 148


# ---------------------------------------------------------------------------
# 3. Config preserved
# ---------------------------------------------------------------------------

def test_configuration_preserved(isolated_runs_dir):
    suites = _representative_suites()
    aggregate = combine_suite_results(suites)
    persist_run(aggregate, suites)
    doc = load(KNOWN_RUN_ID)

    py = next(s for s in doc["suites"] if s["suite"] == "python")
    assert py["configuration_fingerprint"] == KNOWN_FINGERPRINT
    assert py["effective_context_capacity"] == 262144
    assert py["requested_max_output_tokens"] == 196608
    assert py["max_output_tokens"] == 196608
    assert py["output_budget_policy"] == "75_percent_context"
    assert py["output_budget_percent"] == 75
    assert py["model_max_context"] == 262144
    assert py["loaded_context"] == 262144
    assert py["reasoning_policy"] == "inherit"
    assert doc["reasoning_policy"] == "inherit"


# ---------------------------------------------------------------------------
# 4. Telemetry preserved; missing stays missing (never zeroed)
# ---------------------------------------------------------------------------

def test_telemetry_preserved_and_absent_stays_null(isolated_runs_dir):
    suites = _representative_suites()
    # A suite with NO telemetry overrides keeps its dataclass defaults.
    clean = _make_suite("java", passed=48)
    suites[1] = clean

    aggregate = combine_suite_results(suites)
    persist_run(aggregate, suites)
    doc = load(KNOWN_RUN_ID)

    py = next(s for s in doc["suites"] if s["suite"] == "python")
    assert py["ttft_seconds"] == 1.25
    assert py["prefill_throughput"] == 8200.0
    assert py["decode_throughput"] == 1503.5
    assert py["reasoning_tokens"] == 42

    java = next(s for s in doc["suites"] if s["suite"] == "java")
    # Defaults: these were never set -> must remain exactly as produced (None).
    assert java["ttft_seconds"] is None
    assert java["prefill_throughput"] is None
    assert java["decode_throughput"] is None


# ---------------------------------------------------------------------------
# 5. Exact textual evidence -- leading/trailing whitespace, newlines, terminal NL
# ---------------------------------------------------------------------------

def test_exact_textual_evidence_preserved(isolated_runs_dir):
    messy = "  \n\tindent here\n\n\nmultiple\nblank\nlines\ttrailing \n"
    suites = [
        _make_suite("markdown", passed=19,
                    validation_failures=[
                        {"case_id": "md_x", "name": "",
                         "failure_type": "lint", "failure_reason": messy},
                    ],
                    requests=[{
                        "request_id": "req-md-1",
                        "extraction_success": True,
                        "expected_text": "  \n\tindent here\n\n\nmultiple\nblank\nlines\ttrailing \n",
                        "actual_text": "  \n\tindent here\n\n\nmultiple\nblank\nlines\ttrailing \n",
                    }]),
        _make_suite("python", passed=55),
        _make_suite("java", passed=48),
        _make_suite("evidence", passed=22),
        _make_suite("drift", passed=4),
    ]
    aggregate = combine_suite_results(suites)
    persist_run(aggregate, suites)
    doc = load(KNOWN_RUN_ID)

    md = next(s for s in doc["suites"] if s["suite"] == "markdown")
    assert md["validation_failures"][0]["failure_reason"] == messy
    req = md["requests"][0]
    assert req["expected_text"] == messy
    assert req["actual_text"] == messy

    # The persisted JSON serialises the value verbatim (control chars are escaped,
    # as json always does) with no whitespace/newline normalisation applied.
    raw = artifact_path(KNOWN_RUN_ID).read_text(encoding="utf-8")
    assert json.dumps(messy, ensure_ascii=False) in raw


# ---------------------------------------------------------------------------
# 6. Unknown run -> explicit not-found
# ---------------------------------------------------------------------------

def test_unknown_run_returns_not_found(isolated_runs_dir):
    assert try_load("run-does-not-exist") is None
    with pytest.raises(V2RunNotFoundError):
        load("run-does-not-exist")


# ---------------------------------------------------------------------------
# 7. Atomic / replacement safety
# ---------------------------------------------------------------------------

def test_successful_write_leaves_no_temp_files(isolated_runs_dir):
    suites = _representative_suites()
    aggregate = combine_suite_results(suites)
    persist_run(aggregate, suites)

    files = [p.name for p in isolated_runs_dir.iterdir()]
    assert files == [f"{KNOWN_RUN_ID}.json"]


def test_failed_write_creates_no_finished_run(isolated_runs_dir, monkeypatch):
    from src import v2_quality_artifact as art

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(art.json, "dump", boom)

    suites = _representative_suites()
    aggregate = combine_suite_results(suites)
    with pytest.raises(RuntimeError):
        persist_run(aggregate, suites)

    # No finished artifact and no leftover temp file.
    assert not artifact_path(KNOWN_RUN_ID).exists()
    leftovers = list(isolated_runs_dir.glob("*.tmp-*"))
    assert leftovers == []


def test_existing_artifact_overwritten_atomically(isolated_runs_dir):
    first = _representative_suites()
    persist_run(combine_suite_results(first), first)

    # Re-persist a different (still canonical) set; the file is replaced, not appended.
    second = [
        _make_suite(suite, passed=denom)  # perfect pass for idempotent shape check
        for suite, denom in CANONICAL_DENOMINATORS.items()
    ]
    persist_run(combine_suite_results(second), second)

    doc = load(KNOWN_RUN_ID)
    assert all(
        doc["aggregate"]["per_suite"][s]["passed"] == doc["aggregate"]["per_suite"][s]["total"]
        for s in SUITES
    )
    # Still exactly one file on disk.
    assert [p.name for p in isolated_runs_dir.iterdir()] == [f"{KNOWN_RUN_ID}.json"]


def test_atomic_write_helper_preserves_exact_text(tmp_path):
    path = tmp_path / "exact.json"
    payload = {"text": "  \n\n lead/trail \t\n more\n\n"}
    _atomic_write_json(path, payload)
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["text"] == payload["text"]


# ---------------------------------------------------------------------------
# 8. Legacy isolation -- the new persistence path never reaches the executor
# ---------------------------------------------------------------------------

def test_artifact_module_never_imports_or_calls_legacy_executor():
    import inspect
    import re

    from src import v2_quality_artifact as art

    source = inspect.getsource(art)
    # Hard prohibition: no import of the superseded live executor module...
    assert not re.search(r"\b(import|from)\b[^\n]*v2_quality_executor", source), \
        "artifact persistence must not import the legacy executor"
    # ...and never calls its run_v2_quality_live entry point.
    assert ".run_v2_quality_live(" not in source
    # It only depends on the immutable canonical constants of the suite runner.
    assert "from src.v2_quality_suite_runner import" in source


def test_artifact_load_does_not_invoke_executor(isolated_runs_dir):
    suites = _representative_suites()
    aggregate = combine_suite_results(suites)
    persist_run(aggregate, suites)

    # Loading a persisted document must not touch the executor. It returns cleanly.
    doc = load(KNOWN_RUN_ID)
    assert doc["run_id"] == KNOWN_RUN_ID


# ---------------------------------------------------------------------------
# Data-integrity invariants across identity + schema version
# ---------------------------------------------------------------------------

def test_identity_invariants_across_aggregate_and_suites(isolated_runs_dir):
    suites = _representative_suites()
    aggregate = combine_suite_results(suites)
    persist_run(aggregate, suites)
    doc = load(KNOWN_RUN_ID)

    # Aggregate run id / model / fingerprint match every suite.
    for s in doc["suites"]:
        assert s["run_id"] == doc["run_id"] == aggregate["run_id"]
        assert s["model_identifier"] == aggregate["model_identifier"]
        assert s["configuration_fingerprint"] == aggregate["configuration_fingerprint"]

    # All five canonical suites present exactly once.
    assert sorted(s["suite"] for s in doc["suites"]) == sorted(SUITES)


def test_schema_version_and_type_metadata(isolated_runs_dir):
    suites = _representative_suites()
    aggregate = combine_suite_results(suites)
    persist_run(aggregate, suites)
    doc = load(KNOWN_RUN_ID)

    assert doc["schema_version"] == SCHEMA_VERSION
    assert doc["artifact_type"] == ARTIFACT_TYPE
    assert isinstance(doc.get("generated_at"), str) and doc["generated_at"]


def test_unsupported_schema_is_rejected(isolated_runs_dir):
    # Craft a document with an unknown schema version.
    payload = {
        "schema_version": SCHEMA_VERSION + 99,
        "artifact_type": ARTIFACT_TYPE,
        "run_id": KNOWN_RUN_ID,
        "aggregate": {"checks_total": 166},
        "suites": [],
    }
    persist_document(payload)
    with pytest.raises(V2ArtifactSchemaError):
        load(KNOWN_RUN_ID)
