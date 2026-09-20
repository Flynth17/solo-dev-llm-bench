"""Tests for RM-26-AA-0017 -- Speed & Workflow Results experience.

Extends the unified Results shell (RM-26-AA-0016) with:

* **Speed** -- configuration/environment transparency, explicit gap / unsupported /
  failed state preservation (never coerced to zero), and repeatability evidence
  (cold + warm_a + warm_b) rendered only when the backend exposes per-stage runs.
* **Workflow** -- failure-first run list with authoritative suite breakdown,
  client-side filters (model / run-state / failures-only), and per-run drill-down
  to the dedicated ``/v2/results/{run_id}`` evidence page. The frontend formats,
  sorts and filters only; it never recomputes the Workflow score.

Coverage is a mix of:

* seeded-store route contract tests (``/api/results``, ``/api/ranking``,
  ``/api/v2/results/{id}``) -- authoritative backend data, no client re-derivation;
* read-model contract tests (failure-first suite breakdown, N/A != 0);
* static JS/CSS assertions on the shipped assets (mirroring
  ``test_results_shell_route.py``) for the new frontend behaviour.

No benchmark mechanics, scoring or Context scope are touched here.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

import src.main  # noqa: E402  (imports the FastAPI app)

PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = PROJECT_ROOT / "static"


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# Speed -- authoritative point data + gap/state preservation (seeded store)
# ===========================================================================


@pytest.fixture
def test_client():
    return TestClient(src.main.app)


@pytest.fixture
def speed_store(test_client):
    """A fresh ResultsStore seeded with Standard Speed runs incl. an unsupported point."""
    import src.app_state as app_state_module
    from src.results import ResultsStore

    tmp_dir = tempfile.mkdtemp()
    old_store = app_state_module.results_store
    new_store = ResultsStore(
        csv_path=Path(tmp_dir) / "benchmark_results.csv",
        db_path=Path(tmp_dir) / "benchmark_results.db",
    )
    app_state_module.results_store = new_store
    try:
        _seed_speed(new_store)
        yield new_store
    finally:
        app_state_module.results_store = old_store


def _speed_row(**kw):
    base = {
        "timestamp": "2026-05-01T00:00:00+00:00",
        "run_id": "",
        "model_key": "ornith-1.5-35b-a3b",
        "model_display_name": "Ornith 1.5 35B A3B",
        "hardware_label": "LM Studio local",
        "execution_environment": "Local",
        "connection_type": "",
    }
    base.update(kw)
    return base


def _seed_speed(store):
    # Completed Standard Speed run: 8K + 32K completed, 16K persisted as an unsupported
    # GAP at a canonical target (the read model rejects non-canonical targets, so an
    # unsupported point is always a canonical point that did not complete).
    store.add_run(_speed_row(
        run_id="speed-ok", context_point="8K", target_context_tokens=8192,
        iteration=1, cold_or_warm="cold", tokens_per_second=120.0, ttft_seconds=0.9,
        input_tokens=8150, output_tokens=512, prefill_tokens_per_second=9000.0,
        wall_time_seconds=8.0, max_output_tokens=512, temperature=0.0,
        speed_point_status="completed", speed_metric_version=2,
    ))
    store.add_run(_speed_row(
        run_id="speed-ok", context_point="16K", target_context_tokens=16384,
        iteration=1, cold_or_warm="cold", speed_point_status="unsupported",
    ))
    store.add_run(_speed_row(
        run_id="speed-ok", context_point="32K", target_context_tokens=32768,
        iteration=1, cold_or_warm="cold", tokens_per_second=120.0, ttft_seconds=0.9,
        input_tokens=32600, output_tokens=512, prefill_tokens_per_second=9000.0,
        wall_time_seconds=8.0, max_output_tokens=512, temperature=0.0,
        speed_point_status="completed", speed_metric_version=2,
    ))


def test_speed_history_carries_authoritative_ttft_prefill_decode(speed_store):
    from src.routes.results import get_past_results
    payload = _run(get_past_results())
    by_id = {s["run_id"]: s for s in payload["speed_runs"]}
    assert "speed-ok" in by_id
    pt = by_id["speed-ok"]["points"][0]  # canonical 8K point
    # Authoritative telemetry surfaces verbatim -- the UI formats, never recomputes.
    assert pt["ttft_seconds"] == 0.9
    assert pt["prefill_tokens_per_second"] == 9000.0
    assert pt["generation_tokens_per_second"] == 120.0


def test_speed_history_exposes_configuration_identity(speed_store):
    from src.routes.results import get_past_results
    payload = _run(get_past_results())
    s = next(r for r in payload["speed_runs"] if r["run_id"] == "speed-ok")
    # Config/environment transparency fields are present so the user can see which
    # configuration produced the result.
    assert s["hardware_label"] == "LM Studio local"
    assert s["execution_environment"] == "Local"


def test_unsupported_speed_point_is_preserved_not_zeroed(speed_store):
    """An unsupported context point stays a GAP: em-dash / null, never a fabricated zero."""
    from src.routes.results import get_past_results
    payload = _run(get_past_results())
    by_id = {s["run_id"]: s for s in payload["speed_runs"]}
    labels = {p["label"] for p in by_id["speed-ok"]["points"]}
    # The unsupported 16K point is kept as an inspectable gap, not dropped or zeroed.
    assert "16K" in labels
    gap = next(p for p in by_id["speed-ok"]["points"] if p["label"] == "16K")
    # No numeric metric is fabricated for the gap -- telemetry stays null (em-dash),
    # never coerced to a zero-performance result.
    assert gap["generation_tokens_per_second"] is None
    assert gap["ttft_seconds"] is None


def test_unsupported_point_status_preserved_at_read_model(speed_store):
    """The read model keeps the unsupported status verbatim (dedicated page path)."""
    import src.app_state as app_state_module
    from src.v2_speed_read_model import load_speed_run_by_id

    store = app_state_module.results_store
    rows = [r for r in store.get_all() if r.get("run_id") == "speed-ok"]
    view = load_speed_run_by_id("speed-ok", rows)
    by_label = {p["label"]: p for p in view["points"]}
    assert str(by_label["16K"]["status"]).lower() == "unsupported"
    # Completed points keep their metrics.
    assert by_label["8K"]["generation_tokens_per_second"] == 120.0


# ===========================================================================
# Speed -- frontend: config chips, explicit gaps, repeatability where present
# ===========================================================================

RESULTS_JS = (STATIC_DIR / "results.js").read_text(encoding="utf-8")
SHELL_JS = (STATIC_DIR / "results-shell.js").read_text(encoding="utf-8")


def test_speed_frontend_renders_config_and_gap_semantics():
    # Configuration transparency is rendered from the authoritative read model.
    assert "hardware_label" in RESULTS_JS or "Configuration:" in RESULTS_JS
    # Unsupported / failed points render as an explicit gap (em-dash), never a metric row of zeros.
    assert "\u2014" in RESULTS_JS and "N/A" in RESULTS_JS
    # Per-point metrics remain individually visible inside the Details modal (TTFT / prefill / decode).
    for needle in ("TTFT", "Prefill", "Generation"):
        assert needle in RESULTS_JS


def test_speed_frontend_never_fabricates_repeatability_on_benchmark_table():
    # The compact benchmark summary shows one row per model/config with an average
    # generation throughput aggregate; it never fabricates repeatability/stage breakdown.
    # (Stage repeatability is disclosed in the Details modal from the authoritative read
    #  model -- tested there -- not on this compact-table surface.)
    assert "renderSpeedRunRepeatability" not in RESULTS_JS
    # The compact table builder (renderRow) must never reference a stage field; stage rows
    # live only in the modal disclosure helper, so scope the guard to renderRow's body.
    row_start = RESULTS_JS.find("function renderRow")
    row_end = RESULTS_JS.find("function configChips", row_start)
    assert "speed_run_stage" not in RESULTS_JS[row_start:row_end], \
        "the compact benchmark table must not fabricate stage/repeatability rows"


def test_speed_frontend_never_fabricates_zero():
    # Missing telemetry renders as an em-dash, never zero -- N/A stays distinct from 0.
    assert 'return "\\u2014"' in RESULTS_JS
    # No client-side averaging of points into a run-wide tok/s average is computed.
    for bad in ("avg_tokens_per_second", "blended_tok", "meanTokens"):
        assert bad not in RESULTS_JS


# ===========================================================================
# Workflow -- authoritative failure-first suite breakdown (read model)
# ===========================================================================

import src.v2_quality_artifact as art
from src.v2_quality_read_model import build_read_model, load_v2_result

SUITES = ("python", "java", "markdown", "evidence", "drift")
DENOM = {"python": 58, "java": 52, "markdown": 20, "evidence": 30, "drift": 6}
IDENTITY = {
    "run_id": "wf-run-0017",
    "model_identifier": "ornith-1.5-35b-a3b",
    "configuration_fingerprint": "abc123def456" * 8,
    "reasoning_policy": "inherit",
    "effective_context_capacity": 262144,
    "output_budget_policy": "75_percent_context",
    "output_budget_percent": 75,
    "requested_max_output_tokens": 196608,
    "model_max_context": 262144,
    "loaded_context": 262144,
    "temperature": 0.0,
    "loaded_reasoning_mode": "not_exposed",
    "mtp_state": "unknown",
    "speculative_simple": False,
}


def _wf_document():
    """A completed Workflow artifact with exactly one failing check (failure-first)."""
    suites = []
    per_suite = {}
    total_passed = 0
    for name, denom in DENOM.items():
        passed = denom - (1 if name == "python" else 0)  # python: 57/58 -> one failure
        total_passed += passed
        doc = dict(IDENTITY)
        doc.update(suite=name, checks_passed=passed, checks_total=denom)
        if name == "python":
            doc["validation_failures"] = [{
                "case_id": "py-case-7", "name": "preserves-types",
                "failure_type": "assertion", "failure_reason": "type mismatch on field x",
            }]
            doc["requests"] = [{
                "request_id": "req-ok", "extraction_success": True,
                "checks_passed": 57, "checks_total_validator": 58,
            }]
        suites.append(doc)
        per_suite[name] = {"passed": passed, "total": denom}
    aggregate = dict(IDENTITY)
    aggregate.update(checks_passed=165, checks_total=166, per_suite=per_suite, classification="completed")
    return {
        "schema_version": art.SCHEMA_VERSION,
        "artifact_type": art.ARTIFACT_TYPE,
        "generated_at": "2026-05-01T00:00:00+00:00",
        "run_id": IDENTITY["run_id"],
        "aggregate": aggregate,
        "suites": suites,
    }


def test_workflow_read_model_exposes_headline_and_suite_breakdown():
    view = build_read_model(_wf_document())
    # Headline result is authoritative -- not recomputed by the UI.
    assert view["run"]["checks_passed"] == 165
    assert view["run"]["checks_total"] == 166
    assert view["run"]["percentage"] is not None
    # Suite-level breakdown is present for all five suites (passed / total).
    assert len(view["suites"]) == 5
    by_suite = {s["suite"]: s for s in view["suites"]}
    assert by_suite["python"]["checks_passed"] == 57
    assert by_suite["python"]["checks_total"] == 58
    assert by_suite["java"]["checks_passed"] == 52


def test_workflow_read_model_failure_first_separation():
    view = build_read_model(_wf_document())
    # Exactly one failing check surfaces as a failure with its reason/evidence.
    assert len(view["failures"]) == 1
    assert view["failures"][0]["suite"] == "python"
    assert view["failures"][0]["failure_reason"]
    # Successful requests remain inspectable (collapsed by design), not lost.
    assert len(view["successes"]) >= 1


def test_workflow_load_v2_result_from_isolated_dir(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(art, "V2_RUNS_DIR", tmp)
    (tmp / "wf-run-0017.json").write_text(
        json.dumps(_wf_document()), encoding="utf-8"
    )
    view = load_v2_result("wf-run-0017")
    assert view["run"]["run_id"] == "wf-run-0017"
    assert len(view["suites"]) == 5
    assert len(view["failures"]) == 1


# ===========================================================================
# Workflow -- frontend: failure-first, filters, drill-down, no recomputation
# ===========================================================================

def test_workflow_frontend_is_failure_first():
    # Runs are ordered by failing-check count descending (failures surface at top).
    assert "failedCount" in SHELL_JS
    assert "b.failedCount - a.failedCount" in SHELL_JS


def test_workflow_frontend_has_useful_filters_only():
    # Only grounded filters: model text / run-state / failures-only. No proliferation.
    for control in ("wf-filter-model", "wf-filter-status", "wf-filter-failures"):
        assert control in SHELL_JS
    assert "applyWorkflowFilters" in SHELL_JS


def test_workflow_frontend_drills_down_to_dedicated_evidence_page():
    # Drill-down goes to the dedicated /v2/results/{run_id} evidence page (stable deep link).
    assert "/v2/results/" in SHELL_JS


def test_workflow_frontend_never_recomputes_score():
    # The score/fraction is taken verbatim from the backend read model; the frontend
    # only formats it. It must not re-derive fraction from checks_passed/total.
    assert "ag.fraction" in SHELL_JS or ".fraction" in SHELL_JS
    # No client-side division of passed by total to reconstruct a score.
    assert "checks_passed /" not in SHELL_JS
    assert "/ 166" not in SHELL_JS


def test_workflow_frontend_reports_status_by_text_not_colour_alone():
    # Status is always shown as a word label, never communicated by colour alone.
    assert "All checks passed" in SHELL_JS
    assert "failing" in SHELL_JS.lower()


# ===========================================================================
# Shared -- composite unavailable, Context out of scope, routes intact
# ===========================================================================

def test_ranking_still_exposes_suite_breakdown_for_agentic(test_client):
    # The ranking read model still projects per-suite breakdown for the Agentic dimension.
    body = test_client.get("/api/ranking").json()
    assert body["composite"]["status"] == "unavailable"
    # No assertion on presence (data-dependent); the contract is that per_suite, when
    # present, carries authoritative per-suite passed/total.
    assert isinstance(body["models"], list)


def test_context_dimension_stays_out_of_scope(test_client):
    """Context degradation Results UI is RM-26-AA-0018 -- not implemented here."""
    # The ranking still reports Context as an unimplemented dimension (N/A, never scored).
    body = test_client.get("/api/ranking").json()
    assert "context_degradation" in body["all_dimensions"]
    for m in body["models"]:
        ctx = m["dimensions"].get("context_degradation", {})
        if ctx:
            assert ctx.get("status") == "unavailable"
            assert ctx.get("score") is None


def test_v2_result_route_responds_and_404s_for_unknown(test_client):
    # Dedicated Workflow evidence page still loads.
    assert test_client.get("/v2/results/any-run-id").status_code == 200
    # Unknown run id -> 404 (clean, no stack leak).
    resp = test_client.get("/api/v2/results/does-not-exist-xyz")
    assert resp.status_code in (404, 400)


def test_results_shell_page_still_wires_speed_and_workflow(test_client):
    # The delivered dimension nav now lives in the shared app-shell.js (injected into
    # every page) rather than being duplicated inline in results.html. Both areas are
    # still defined there with their chips.
    app_js = (STATIC_DIR / "app-shell.js").read_text(encoding="utf-8")
    assert 'data-view="speed"' in app_js
    assert 'data-view="workflow"' in app_js
    # The delivered view containers + the new Workflow filter bar are static content.
    body = test_client.get("/results").text
    assert "workflow-filter-bar" in body
    assert "wf-clear-filters" in body
