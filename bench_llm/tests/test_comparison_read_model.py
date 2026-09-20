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


def _speed_points(generation=195.2):
    """Normalized canonical points exactly as the route attaches them (ST-004).

    Mirrors the authoritative single-run read model's point view shape; values are
    present and valid so selection / average contracts can be exercised.
    """
    return [
        {"label": "8K", "target_context_tokens": 8192, "actual_prompt_tokens": 5600,
         "ttft_seconds": 0.9, "prefill_tokens_per_second": 6200.0,
         "generation_tokens_per_second": generation, "completion_tokens": 1024,
         "wall_time_seconds": 5.3, "status": "completed"},
        {"label": "16K", "target_context_tokens": 16384, "actual_prompt_tokens": 11900,
         "ttft_seconds": 1.7, "prefill_tokens_per_second": 7000.0,
         "generation_tokens_per_second": generation - 12.5, "completion_tokens": 1024,
         "wall_time_seconds": 9.8, "status": "completed"},
        {"label": "32K", "target_context_tokens": 32768, "actual_prompt_tokens": 24100,
         "ttft_seconds": 3.4, "prefill_tokens_per_second": 7100.0,
         "generation_tokens_per_second": generation - 25.0, "completion_tokens": 1024,
         "wall_time_seconds": 18.6, "status": "completed"},
    ]


def _speed_run(run_id, model_key="Ornith", quant="Q4_K_M", context=32768,
               metric_version=2, normalizable=True):
    row = {"run_id": run_id, "model_key": model_key}
    for k in _SPEED_IDENTITY_KEYS:
        row[k] = None
    row.update(
        {
            "model_key": model_key,
            "model_quantization": quant,
            "loaded_context": context,
            # Standard Speed point columns so the route's authoritative normalization
            # (load_speed_run_by_id) succeeds end-to-end in the integration tests.
            "context_point": "8K",
            "target_context_tokens": 8192,
            "speed_metric_version": metric_version,
            "tokens_per_second": 195.2,
        }
    )
    return {
        "family": "speed",
        "run_id": run_id,
        "rows": [row],
        "classification": "canonical",
        "status": "completed",
        # ST-004: the route attaches normalized canonical points + run-level metric
        # version. ``normalizable=False`` models a run whose shape the single-run read
        # model cannot represent (points stay None -- never fabricated).
        "points": _speed_points() if normalizable else None,
        "metric_version": metric_version,
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
# Speed metric-version compatibility + projection (ST-004).
# ---------------------------------------------------------------------------


def test_speed_prefers_current_metric_run_over_legacy():
    """A legacy run is never silently substituted when corrected evidence exists."""
    legacy = _speed_run("speed-aaa", "Ornith", "Q4_K_M", metric_version=None)
    current = _speed_run("speed-bbb", "Ornith", "Q4_K_M", metric_version=2)
    state = rm._resolve_single([legacy, current], "speed")
    assert state["state"] == rm.AVAILABLE
    assert state["run_id"] == "speed-bbb"  # the corrected run is selected
    assert state["metric_version"] == 2


def test_speed_legacy_only_reports_legacy_state():
    """No corrected evidence -> honest LEGACY state with traceability, no values."""
    legacy = _speed_run("speed-legacy", "Ornith", "Q4_K_M", metric_version=None)
    state = rm._resolve_single([legacy], "speed")
    assert state["state"] == rm.LEGACY
    assert state["run_id"] == "speed-legacy"
    assert state["deep_link"] == "/speed/results/speed-legacy"
    assert "points" not in state  # legacy values are never projected as comparable
    assert state["metric_version"] is None


def test_speed_legacy_v1_reports_legacy_state():
    v1 = _speed_run("speed-v1", "Ornith", metric_version=1)
    state = rm._resolve_single([v1], "speed")
    assert state["state"] == rm.LEGACY
    assert state["metric_version"] == 1


def test_speed_non_normalizable_current_run_reports_unavailable():
    """Current-metric evidence whose shape the read model cannot represent is not
    fabricated into values -- it reports UNAVAILABLE with traceability."""
    run = _speed_run("speed-multistage", "Ornith", metric_version=3, normalizable=False)
    state = rm._resolve_single([run], "speed")
    assert state["state"] == rm.UNAVAILABLE
    assert state["metric_version"] == 3
    assert state["run_id"] == "speed-multistage"


def test_speed_available_projects_points_and_average():
    state = rm._resolve_single([_speed_run("s-or", "Ornith")], "speed")
    assert state["state"] == rm.AVAILABLE
    labels = [p["label"] for p in state["points"]]
    assert labels == ["8K", "16K", "32K"]  # canonical order preserved
    by_label = {p["label"]: p for p in state["points"]}
    assert by_label["8K"]["generation_tokens_per_second"] == 195.2
    assert by_label["8K"]["actual_prompt_tokens"] == 5600
    assert by_label["8K"]["status"] == "completed"
    # Presentation aggregate: mean of present valid canonical points (never a score).
    expected = (195.2 + 182.7 + 170.2) / 3
    assert state["average_generation_tps"] is not None
    assert abs(state["average_generation_tps"] - expected) < 1e-9


def test_speed_average_excludes_missing_and_unsupported_points():
    points = _speed_points()
    points[0]["generation_tokens_per_second"] = None      # missing -> excluded, never zero
    points[1]["status"] = "unsupported"                   # unsupported -> excluded
    points[1]["generation_tokens_per_second"] = 999.0    # ...even when a value is stored
    avg = rm.average_generation_tps(points)
    assert abs(avg - (points[2]["generation_tokens_per_second"])) < 1e-9


def test_speed_average_is_none_when_no_valid_points():
    points = _speed_points()
    for p in points:
        p["generation_tokens_per_second"] = None
    assert rm.average_generation_tps(points) is None
    assert rm.average_generation_tps([]) is None
    assert rm.average_generation_tps(None) is None


def test_speed_average_ignores_non_canonical_labels():
    points = _speed_points() + [{"label": "64K", "generation_tokens_per_second": 999.0}]
    expected = (195.2 + 182.7 + 170.2) / 3
    assert abs(rm.average_generation_tps(points) - expected) < 1e-9


def test_speed_failed_run_still_surfaces_as_failed():
    failed = _speed_run("s-fail", "Ornith")
    failed["classification"] = "failed"
    failed["status"] = "failed"
    state = rm._resolve_single([failed], "speed")
    assert state["state"] == rm.FAILED


def test_same_model_diffconfig_speed_pins_distinct_runs_with_metrics():
    """Same base model, different configs: each side pins its own run and projects its
    own values -- never collapsed because the base model matches."""
    q4 = _speed_run("speed-q4", "Ornith", "Q4_K_M")
    q5 = _speed_run("speed-q5", "Ornith", "Q5_K_M")
    keys = _catalogue_keys([q4, q5], [], [])
    assert len(keys) == 2
    result = rm.resolve_comparison(keys[0], keys[1], [q4, q5], [], [])
    dims = result["comparison"]["dimensions"]["speed"]
    assert dims["subject_a"]["state"] == rm.AVAILABLE
    assert dims["subject_b"]["state"] == rm.AVAILABLE
    assert {dims["subject_a"]["run_id"], dims["subject_b"]["run_id"]} == {"speed-q4", "speed-q5"}
    # Each side carries its own values -- no shared run duplicated into both columns.
    assert dims["subject_a"]["points"] is not None and dims["subject_b"]["points"] is not None


# ---------------------------------------------------------------------------
# Workflow dimension projection (v2 Quality) -- run-level pass rate + per-suite.
# ---------------------------------------------------------------------------


def _wf_run(run_id, model="Ornith", fingerprint="WF-A", generated_at="2026-05-01T00:00:00+00:00",
            classification="success", checks_passed=148, checks_total=166, percentage=89.2,
            suites=None):
    """A Workflow candidate carrying the full build_read_model view (nested run + suites)."""
    if suites is None:
        suites = [
            {"suite": "python", "checks_passed": 55, "checks_total": 58, "failed_checks": 3},
            {"suite": "java", "checks_passed": 48, "checks_total": 52, "failed_checks": 4},
        ]
    return {
        "family": "workflow",
        "run_id": run_id,
        "generated_at": generated_at,
        "view": {
            "run": {
                "model_identifier": model,
                "configuration_fingerprint": fingerprint,
                "classification": classification,
                "checks_passed": checks_passed,
                "checks_total": checks_total,
                "percentage": percentage,
            },
            "suites": suites,
        },
    }


def test_workflow_available_projects_aggregate_pass_rate():
    """An AVAILABLE Workflow run projects its authoritative run-level pass rate --
    read verbatim from build_read_model, never recomputed here."""
    state = rm._resolve_single(
        [_wf_run("wf-1", checks_passed=148, checks_total=166, percentage=89.2)], "workflow"
    )
    assert state["state"] == rm.AVAILABLE
    assert state["checks_passed"] == 148
    assert state["checks_total"] == 166
    assert abs(state["percentage"] - 89.2) < 1e-9
    # The authoritative run classification is surfaced on the projection too.
    assert state["classification"] == "success"


def test_workflow_projects_per_suite_breakdown():
    """The per-suite breakdown carries suite + checks passed/total + failed count,
    verbatim from build_read_model's suites array."""
    suites = [
        {"suite": "python", "checks_passed": 55, "checks_total": 58, "failed_checks": 3},
        {"suite": "java", "checks_passed": 52, "checks_total": 52, "failed_checks": 0},
    ]
    state = rm._resolve_single([_wf_run("wf-1", suites=suites)], "workflow")
    proj_suites = state["suites"]
    assert len(proj_suites) == 2
    for got, want in zip(proj_suites, suites):
        assert got["suite"] == want["suite"]
        assert got["checks_passed"] == want["checks_passed"]
        assert got["checks_total"] == want["checks_total"]
        assert got["failed_checks"] == want["failed_checks"]


def test_workflow_missing_check_data_is_honest_none():
    """A Workflow run whose view carries no check counts projects None (never a
    fabricated number) and an empty suite list -- the UI renders an honest gap."""
    state = rm._resolve_single([_wf("wf-1", "Ornith")], "workflow")  # flat view, no checks
    assert state["state"] == rm.AVAILABLE
    assert state["checks_passed"] is None
    assert state["checks_total"] is None
    assert state["percentage"] is None
    assert state["suites"] == []


def test_workflow_failed_run_never_projects_numbers():
    """A failed run stays FAILED with no aggregate / suite projection -- only its
    traceability (state, run_id, deep_link)."""
    failed = _wf_run("wf-fail", classification="failed")
    state = rm._resolve_single([failed], "workflow")
    assert state["state"] == rm.FAILED
    assert "checks_passed" not in state
    assert "suites" not in state


def test_workflow_check_counts_are_int_not_bool():
    """Check counts project as plain ints; a bool is rejected -> None (never 0/1)."""
    state = rm._resolve_single(
        [_wf_run("wf-1", checks_passed=True, checks_total=166)], "workflow"
    )
    assert state["checks_passed"] is None  # bool rejected, never coerced to 1
    assert state["checks_total"] == 166


def test_workflow_percentage_coerces_int_to_float():
    """An integer percentage still projects as a float (matching build_read_model)."""
    state = rm._resolve_single([_wf_run("wf-1", percentage=90)], "workflow")
    assert state["percentage"] == 90.0
    assert isinstance(state["percentage"], float)


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


def test_route_prefers_current_metric_evidence_end_to_end():
    """Phase 2 contract through the real route: a subject with BOTH legacy and current
    runs resolves to the corrected run; a legacy-only subject reports LEGACY (never
    silently compared)."""
    client = TestClient(src.main.app)

    # Ornith Q4: one legacy run + one current-metric run of the SAME configuration.
    src.app_state.results_store.add_run(_speed_run("speed-ornith-legacy", "Ornith", "Q4_K_M", metric_version=None)["rows"][0])
    src.app_state.results_store.add_run(_speed_run("speed-ornith-current", "Ornith", "Q4_K_M", metric_version=2)["rows"][0])
    # Llama: legacy-only evidence.
    src.app_state.results_store.add_run(_speed_run("speed-llama-legacy", "Llama", "Q8_0", metric_version=None)["rows"][0])

    subjects = client.get("/api/comparison/subjects").json()["subjects"]
    ornith_key = next(s["subject_key"] for s in subjects if s["model_version"] == "Ornith")
    llama_key = next(s["subject_key"] for s in subjects if s["model_version"] == "Llama")

    resolved = client.get("/api/comparison", params={"a": ornith_key, "b": llama_key}).json()
    dims = resolved["comparison"]["dimensions"]["speed"]

    # Ornith: the corrected run is selected -- legacy never silently substituted.
    assert dims["subject_a"]["state"] == rm.AVAILABLE
    assert dims["subject_a"]["run_id"] == "speed-ornith-current"
    assert dims["subject_a"]["metric_version"] == 2
    # The fixture row persists a single canonical point; normalization surfaces exactly it.
    points = dims["subject_a"]["points"]
    assert [p["label"] for p in points] == ["8K"]

    # Llama: legacy-only evidence -> explicit LEGACY state with traceability, no values.
    assert dims["subject_b"]["state"] == rm.LEGACY
    assert dims["subject_b"]["run_id"] == "speed-llama-legacy"
    assert dims["subject_b"]["deep_link"] == "/speed/results/speed-llama-legacy"
    assert "points" not in dims["subject_b"]
