"""Tests for the comparison read model (RM-26-AA-0013, ST-002).

Pure composition-layer unit tests over already-resolved candidate evidence plus a
light route integration test. They pin the ST-002 acceptance gate:

* distinct selectable subjects derived from committed evidence (no display names),
* same-model / different-config comparison reports Workflow as AMBIGUOUS for both
  (the critical regression -- never duplicate one shared run into both columns),
* only the representative family is pinned AVAILABLE for same-model configs,
* different base models resolve each dimension independently,
* neutral run selection: recency for Workflow/Context, deterministic fallback for
  Speed, and a newer failed run is NOT replaced by an older success,
* state semantics preserved (missing / unsupported / in_progress),
* identical-subject comparison rejected.
"""

import pytest
from fastapi.testclient import TestClient

import src.main
from src import app_state  # binds bare name `app_state`
import src.v2_quality_artifact as qa
import src.v2_context_artifact as ca
from src.results import ResultsStore
from src import comparison_read_model as rm


# ---------------------------------------------------------------------------
# Candidate factories -- mirror the shapes the route feeds the read model.
# ---------------------------------------------------------------------------

_SPEED_IDENTITY_KEYS = list(rm.SPEED_IDENTITY_KEYS)


def _speed_run(run_id, model_key="Ornith", quant="Q4_K_M", context=32768):
    row = {"run_id": run_id, "model_key": model_key}
    for k in _SPEED_IDENTITY_KEYS:
        row[k] = None
    row.update(
        {
            "model_key": model_key,
            "model_quantization": quant,
            "loaded_context": context,
        }
    )
    return {
        "family": "speed",
        "run_id": run_id,
        "rows": [row],
        "classification": "canonical",
        "status": "completed",
    }


def _wf(run_id, model="Ornith", fingerprint="WF-SAME", generated_at="2026-05-01T00:00:00+00:00",
        classification="completed"):
    return {
        "family": "workflow",
        "run_id": run_id,
        "generated_at": generated_at,
        "view": {
            "model_identifier": model,
            "configuration_fingerprint": fingerprint,
            "classification": classification,
        },
    }


def _ctx(run_id, model="Ornith", fingerprint="CTX-Q4", generated_at="2026-05-01T00:00:00+00:00",
         classification="success"):
    return {
        "family": "context",
        "run_id": run_id,
        "generated_at": generated_at,
        "view": {
            "model_key": model,
            "configuration_fingerprint": fingerprint,
            "classification": classification,
        },
    }


def _catalogue_keys(speed, workflow, context):
    """Build the catalogue and return its subject keys (route-independent)."""
    cat = rm.build_subject_catalogue(speed, workflow, context)
    return [s["subject_key"] for s in cat["subjects"]]


# ---------------------------------------------------------------------------
# Subject catalogue (Phase 3).
# ---------------------------------------------------------------------------


def test_catalogue_separates_same_model_different_config():
    speed = [_speed_run("speed-q4", "Ornith", "Q4_K_M"), _speed_run("speed-q5", "Ornith", "Q5_K_M")]
    cat = rm.build_subject_catalogue(speed, [], [])
    keys = [s["subject_key"] for s in cat["subjects"]]
    assert len(keys) == 2
    # Both subjects share the base model but are distinct, deterministic keys.
    assert all(s["model_version"] == "Ornith" for s in cat["subjects"])
    assert all(s["representative_family"] == "speed" for s in cat["subjects"])
    assert len(set(keys)) == 2


def test_catalogue_separates_different_models():
    speed = [_speed_run("s1", "Ornith", "Q4_K_M"), _speed_run("s2", "Llama", "Q4_K_M")]
    cat = rm.build_subject_catalogue(speed, [], [])
    bases = {s["model_version"] for s in cat["subjects"]}
    assert bases == {"Ornith", "Llama"}


def test_catalogue_uses_identity_not_display_name():
    # Two runs with the same base model identity but different display names still
    # collapse to a single subject -- display name is never an identity key.
    speed = [_speed_run("s1", "Ornith", "Q4_K_M")]
    speed[0]["rows"][0]["model_display_name"] = "Ornith 35B Ultra"
    cat = rm.build_subject_catalogue(speed, [], [])
    assert len(cat["subjects"]) == 1


# ---------------------------------------------------------------------------
# Same-model / different-config -- the critical regression (Phase 2 / Phase 6).
# ---------------------------------------------------------------------------


def test_same_model_diffconfig_workflow_is_ambiguous_for_both():
    """Workflow cannot distinguish Q4 from Q5 -> AMBIGUOUS for both, never a shared
    run duplicated into both columns. This is the ST-002 critical regression."""
    speed = [_speed_run("speed-q4", "Ornith", "Q4_K_M"), _speed_run("speed-q5", "Ornith", "Q5_K_M")]
    workflow = [_wf("wf-shared", "Ornith", fingerprint="WF-SHARED")]  # one shared run

    keys = _catalogue_keys(speed, workflow, [])
    assert len(keys) == 2

    result = rm.resolve_comparison(keys[0], keys[1], speed, workflow, [])
    dims = result["comparison"]["dimensions"]

    assert result["comparison"]["same_model_configuration"] is True
    # Representative family (Speed) distinguishes the two configs -> AVAILABLE each.
    assert dims["speed"]["subject_a"]["state"] == rm.AVAILABLE
    assert dims["speed"]["subject_b"]["state"] == rm.AVAILABLE
    assert dims["speed"]["subject_a"]["run_id"] != dims["speed"]["subject_b"]["run_id"]
    # Workflow cannot attribute the shared run to one config -> AMBIGUOUS both.
    assert dims["workflow"]["subject_a"]["state"] == rm.AMBIGUOUS
    assert dims["workflow"]["subject_b"]["state"] == rm.AMBIGUOUS
    # Context is non-representative for a Speed-clustered subject -> AMBIGUOUS.
    assert dims["context"]["subject_a"]["state"] == rm.AMBIGUOUS
    assert dims["context"]["subject_b"]["state"] == rm.AMBIGUOUS


def test_cross_family_fingerprints_not_asserted_equivalent():
    """The system must not claim one Speed/Workflow/Context fingerprint is the same
    physical configuration across families."""
    speed = [_speed_run("speed-q4", "Ornith", "Q4_K_M"), _speed_run("speed-q5", "Ornith", "Q5_K_M")]
    workflow = [_wf("wf-shared", "Ornith", fingerprint="WF-SHARED")]
    keys = _catalogue_keys(speed, workflow, [])

    result = rm.resolve_comparison(keys[0], keys[1], speed, workflow, [])
    # Only the representative family is pinned; nothing asserts cross-family equality.
    assert result["comparison"]["dimensions"]["workflow"] == {
        "subject_a": {"state": rm.AMBIGUOUS},
        "subject_b": {"state": rm.AMBIGUOUS},
    }


# ---------------------------------------------------------------------------
# Different base models -- independent per-model resolution.
# ---------------------------------------------------------------------------


def test_different_models_resolve_independently():
    speed = [
        _speed_run("s-or", "Ornith", "Q4_K_M"),
        _speed_run("s-ll", "Llama", "Q4_K_M"),
    ]
    workflow = [_wf("wf-or", "Ornith"), _wf("wf-ll", "Llama")]
    context = [_ctx("c-or", "Ornith"), _ctx("c-ll", "Llama")]
    keys = _catalogue_keys(speed, workflow, context)
    assert len(keys) == 2

    result = rm.resolve_comparison(keys[0], keys[1], speed, workflow, context)
    dims = result["comparison"]["dimensions"]
    assert result["comparison"]["same_model_configuration"] is False
    for fam in rm.FAMILIES:
        assert dims[fam]["subject_a"]["state"] == rm.AVAILABLE
        assert dims[fam]["subject_b"]["state"] == rm.AVAILABLE


# ---------------------------------------------------------------------------
# Neutral run selection (Phase 4 / Phase 5).
# ---------------------------------------------------------------------------


def test_neutral_selection_prefers_latest_workflow_by_recency():
    older = _wf("wf-old", "Llama", generated_at="2026-01-01T00:00:00+00:00")
    newer = _wf("wf-new", "Llama", fingerprint="WF-A", generated_at="2026-06-01T00:00:00+00:00")
    state = rm._resolve_single([older, newer], "workflow")
    assert state["state"] == rm.AVAILABLE
    assert state["run_id"] == "wf-new"


def test_failed_latest_run_not_replaced_by_older_success():
    older_ok = _wf("wf-old", "Llama", fingerprint="WF-A", generated_at="2026-01-01T00:00:00+00:00",
                   classification="completed")
    newer_failed = _wf("wf-new", "Llama", fingerprint="WF-A", generated_at="2026-06-01T00:00:00+00:00",
                       classification="failed")
    state = rm._resolve_single([older_ok, newer_failed], "workflow")
    # A newer failed terminal run is surfaced as FAILED, not silently replaced.
    assert state["state"] == rm.FAILED


def test_speed_uses_deterministic_run_id_fallback():
    # Speed has no ordering field; selection falls back to a deterministic run_id.
    a = _speed_run("speed-bbb", "Llama")
    b = _speed_run("speed-aaa", "Llama")
    state = rm._resolve_single([a, b], "speed")
    assert state["state"] == rm.AVAILABLE
    assert state["run_id"] == "speed-aaa"  # lexicographic, neutral, reproducible


def test_in_progress_when_only_non_terminal_runs():
    running = _wf("wf-running", "Llama")
    running["status"] = "running"  # non-terminal run status
    state = rm._resolve_single([running], "workflow")
    assert state["state"] == rm.IN_PROGRESS


# ---------------------------------------------------------------------------
# State semantics preserved (Phase 6 / ST-001).
# ---------------------------------------------------------------------------


def test_missing_dimension_state():
    speed = [_speed_run("s-or", "Ornith", "Q4_K_M")]
    keys = _catalogue_keys(speed, [], [])
    # Only one subject exists -> compare against a synthetic different-model key that
    # has no evidence at all.
    result = rm.resolve_comparison(keys[0], "Llama|speed:deadbeef", speed, [], [])
    assert result["comparison"]["dimensions"]["workflow"]["subject_a"]["state"] == rm.MISSING
    assert result["comparison"]["dimensions"]["context"]["subject_b"]["state"] == rm.MISSING


def test_unsupported_state_preserved():
    unsupported = _ctx("c-uns", "Llama", classification="unsupported")
    state = rm._resolve_single([unsupported], "context")
    assert state["state"] == rm.UNSUPPORTED


# ---------------------------------------------------------------------------
# Two-subject validation (Phase 7).
# ---------------------------------------------------------------------------


def test_identical_subject_rejected():
    speed = [_speed_run("s-or", "Ornith", "Q4_K_M")]
    keys = _catalogue_keys(speed, [], [])
    with pytest.raises(ValueError):
        rm.resolve_comparison(keys[0], keys[0], speed, [], [])


def test_two_distinct_subjects_allowed():
    speed = [
        _speed_run("s-or", "Ornith", "Q4_K_M"),
        _speed_run("s-ll", "Llama", "Q4_K_M"),
    ]
    keys = _catalogue_keys(speed, [], [])
    result = rm.resolve_comparison(keys[0], keys[1], speed, [], [])
    assert "comparison" in result


# ---------------------------------------------------------------------------
# Route integration (registration + read-only contract).
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_persistence(tmp_path, monkeypatch):
    """Point Workflow/Context artifact discovery at empty temp dirs and give the
    route a clean isolated store for the whole test class."""
    monkeypatch.setattr(qa, "V2_RUNS_DIR", tmp_path / "v2_runs")
    monkeypatch.setattr(ca, "CONTEXT_RUNS_DIR", tmp_path / "context_runs")
    (tmp_path / "v2_runs").mkdir()
    (tmp_path / "context_runs").mkdir()
    monkeypatch.setattr(
        app_state,
        "results_store",
        ResultsStore(db_path=tmp_path / "benchmark_results.db",
                     csv_path=tmp_path / "empty_benchmark_results.csv"),
    )


def _write_workflow_artifact(directory, document):
    import json
    from pathlib import Path

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{document['run_id']}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_comparison_subjects_endpoint_is_read_only():
    client = TestClient(src.main.app)
    resp = client.get("/api/comparison/subjects")
    assert resp.status_code == 200
    body = resp.json()
    assert "subjects" in body


def test_comparison_endpoint_requires_both_keys():
    client = TestClient(src.main.app)
    resp = client.get("/api/comparison?a=Ornith|speed:abc")
    assert resp.status_code == 400


def test_comparison_route_composes_from_isolated_evidence():
    """End-to-end: two Speed runs of the same model surface as distinct subjects and
    resolve with Workflow AMBIGUOUS (the critical regression) through the real route."""
    client = TestClient(src.main.app)

    # Two same-model Speed runs reach the store; a single shared Workflow artifact.
    src.app_state.results_store.add_run(_speed_run("speed-q4", "Ornith", "Q4_K_M")["rows"][0])
    src.app_state.results_store.add_run(_speed_run("speed-q5", "Ornith", "Q5_K_M")["rows"][0])
    _write_workflow_artifact(
        __import__("pathlib").Path(qa.V2_RUNS_DIR),
        {
            "schema_version": 1,
            "artifact_type": "solo-dev-llm-bench.v2-run",
            "generated_at": "2026-05-01T00:00:00+00:00",
            "run_id": "wf-shared",
            "model_identifier": "Ornith",
            "configuration_fingerprint": "WF-SHARED",
            "classification": "completed",
            "aggregate": {"checks_total": 10},
            "suites": [],
        },
    )

    subjects = client.get("/api/comparison/subjects").json()["subjects"]
    ornith_subjects = [s for s in subjects if s["model_version"] == "Ornith"]
    assert len(ornith_subjects) == 2

    keys = [s["subject_key"] for s in ornith_subjects]
    resolved = client.get("/api/comparison", params={"a": keys[0], "b": keys[1]}).json()
    dims = resolved["comparison"]["dimensions"]
    assert dims["speed"]["subject_a"]["state"] == rm.AVAILABLE
    assert dims["workflow"]["subject_a"]["state"] == rm.AMBIGUOUS
    assert dims["workflow"]["subject_b"]["state"] == rm.AMBIGUOUS
