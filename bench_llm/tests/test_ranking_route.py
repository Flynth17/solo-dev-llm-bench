"""HTTP route tests for src/routes/ranking.py (Results UI prerequisite).

Exercises the real store/read-model -> HTTP path against a throwaway artifact
directory (no LM Studio, no live execution). Covers status mapping, composition of
Speed + Workflow evidence, integrity-skip behaviour (one bad workflow artifact must
not collapse the surface), and explicit composite-unavailable design.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import src.main
from src import app_state  # binds bare name `app_state`; used by the isolated-store fixture below
import src.v2_quality_artifact as art
from src.ranking_read_model import (
    DIMENSION_AGENtic,
    DIMENSION_INTELLIGENCE,
    DIMENSION_SPEED,
)
from src.results import ResultsStore
from src.v2_quality_read_model import (
    CANONICAL_DENOMINATORS,
    SUITES,
    TOTAL_DENOMINATOR,
)
from src.routes.ranking import _load_agentic_views

client = TestClient(src.main.app)


# ---------------------------------------------------------------------------
# Fixtures: throwaway Runs dir + a clean isolated store.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_runs_dir(tmp_path, monkeypatch):
    """Point V2 artifact discovery at an empty temp dir for the whole test."""
    monkeypatch.setattr(art, "V2_RUNS_DIR", tmp_path)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Isolated ResultsStore backed by a throwaway DB; point the route's singleton at it.

    The ranking route reads ``src.app_state.results_store`` lazily (imported inside
    the handler), so patching the module-level singleton attribute is enough to make
    every endpoint observe this clean store instead of the shared real database. Each
    test gets its own tmp_path, so no rows leak between tests.
    """
    # Isolate BOTH persistence surfaces. ResultsStore.__init__ migrates its CSV path into
    # the DB and loads from the DB (source of truth), so swapping db_path alone is not
    # enough -- it would still ingest the real benchmark_results.csv. Point the store at a
    # throwaway empty CSV so only rows added via add_run() are observed.
    monkeypatch.setattr(
        app_state, "results_store",
        ResultsStore(db_path=tmp_path / "benchmark_results.db",
                     csv_path=tmp_path / "empty_benchmark_results.csv"),
    )
    return app_state.results_store


@pytest.fixture()
def speed_rows():
    """A minimal valid Standard Speed run (8K/16K/32K) persistable to a store."""
    def _run(run_id, model_key, exp=None):
        rows = []
        pairs = [
            (8192, 350.0, 360.0),
            (16384, 250.0, 260.0),
            (32768, 150.0, 160.0),
        ]
        for idx, (target, wa, wb) in enumerate(pairs):
            rows.append(_speed_row(run_id, model_key, target, None, stage="cold",
                                   num_experts=exp))
            rows.append(_speed_row(run_id, model_key, target, wa, stage="warm_a",
                                   num_experts=exp))
            rows.append(_speed_row(run_id, model_key, target, wb, stage="warm_b",
                                   num_experts=exp))
        return rows

    return _run


def _speed_row(run_id, model_key, target, warm_gen, *, stage="cold", num_experts=None):
    is_cold = stage == "cold"
    return {
        "run_id": run_id,
        "model_key": model_key,
        "model_display_name": model_key,
        "target_context_tokens": target,
        "speed_run_stage": stage,
        "tokens_per_second": warm_gen if not is_cold else 0.0,
        "ttft_seconds": 1.2 if is_cold else None,
        "prefill_tokens_per_second": 4000.0 if is_cold else None,
        "input_tokens": target,
        "speed_point_status": "completed",
        "model_quantization": "q4_k_m",
        "reasoning_mode": "off",
        "kv_cache_k_quantization": "unknown",
        "kv_cache_v_quantization": "unknown",
        "flash_attention": True,
        "offload_kv_cache_to_gpu": False,
        "eval_batch_size": 4,
        "physical_batch_size": 4,
        "parallel": 1,
        "cpu_model": "test-cpu",
        "loaded_context": 262144,
        "model_max_context": 262144,
        "num_experts": num_experts,
        "configuration_fingerprint": f"fp-{run_id}",
    }


# ---------------------------------------------------------------------------
# Helpers: persist a Workflow artifact into the throwaway dir.
# ---------------------------------------------------------------------------

def _persist_workflow(run_id, model_identifier, passed, total, fingerprint="fp-q",
                      classification="canonical", reasoning_policy="inherit"):
    """Persist a workflow artifact into the (isolated) throwaway dir.

    Emits a document that is fully compliant with what ``load_v2_result`` /
    ``build_read_model`` validate, so a *valid* run contributes to the Agentic
    dimension. Per-suite denominators come from the locked canonical constants
    rather than being hard-coded here; per-suite ``passed`` values are derived
    proportionally from the aggregate score (clamped to each denominator) so they
    never exceed their total and stay internally consistent with the aggregate.

    An artifact is treated as invalid by the reader when its aggregate
    ``checks_total`` differs from the canonical TOTAL_DENOMINATOR, so callers that
    want an intentionally-skipped artifact simply pass a non-canonical total.
    """
    denoms = {s: CANONICAL_DENOMINATORS[s] for s in SUITES}
    per_suite: dict[str, dict[str, int]] = {}
    for suite in SUITES:
        denom = denoms[suite]
        derived = int(round(passed * denom / total)) if total else 0
        derived = max(0, min(derived, denom))
        per_suite[suite] = {"passed": derived, "total": denom}

    identity = {
        "run_id": run_id,
        "model_identifier": model_identifier,
        "configuration_fingerprint": fingerprint,
        "reasoning_policy": reasoning_policy,
    }
    suites = [
        {**identity, "suite": suite,
         "checks_passed": per_suite[suite]["passed"],
         "checks_total": per_suite[suite]["total"]}
        for suite in SUITES
    ]
    doc = {
        "schema_version": art.SCHEMA_VERSION,
        "artifact_type": art.ARTIFACT_TYPE,
        "run_id": run_id,
        "model_identifier": model_identifier,
        "configuration_fingerprint": fingerprint,
        "classification": classification,
        "reasoning_policy": reasoning_policy,
        "aggregate": {
            **identity,
            "classification": classification,
            "checks_passed": passed,
            "checks_total": total,
            "per_suite": per_suite,
        },
        "suites": suites,
    }
    path = art.V2_RUNS_DIR / f"{run_id}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return run_id


# ---------------------------------------------------------------------------
# Endpoints: shape + composition.
# ---------------------------------------------------------------------------

def test_ranking_endpoint_returns_200_and_composes_speed_plus_agentic(store, speed_rows):
    for r in speed_rows("run-speed-1", "model-x"):
        store.add_run(r)
    _persist_workflow("run-qual-1", "model-x", 148, 166)

    resp = client.get("/api/ranking")
    assert resp.status_code == 200
    body = resp.json()

    # Both dimensions are advertised as available once evidence exists.
    assert set(body["available_dimensions"]) == {DIMENSION_SPEED, DIMENSION_AGENtic}
    assert len(body["models"]) == 1
    m = body["models"][0]
    dim = m["dimensions"]
    assert dim[DIMENSION_SPEED]["status"] == "available"
    assert dim[DIMENSION_AGENtic]["status"] == "available"


def test_composite_is_explicitly_unavailable_from_api(store, speed_rows):
    for r in speed_rows("run-speed-2", "model-y"):
        store.add_run(r)

    body = client.get("/api/ranking").json()
    assert body["composite"]["status"] == "unavailable"
    # Reserved placeholder is null, never an inferred number.
    assert body["models"][0]["overall_solo_bench_score"] is None


def test_ranking_models_summary_and_lists(store, speed_rows):
    for r in speed_rows("run-speed-3", "model-z"):
        store.add_run(r)

    models = client.get("/api/ranking/models").json()
    assert len(models["models"]) == 1
    entry = models["models"][0]
    assert set(entry.keys()) == {
        "model_version", "model_family", "architecture", "rank", "has_approved_evidence"
    }

    summary = client.get("/api/ranking/summary").json()
    assert len(summary["models"]) == 1
    # Summary exposes dimension availability per model.
    assert "dimensions" in summary["models"][0]


def test_architecture_distinguished_dense_vs_moe_from_api(store, speed_rows):
    for r in speed_rows("run-dense", "model-arch"):
        store.add_run(r)  # no num_experts -> unknown/absent architecture bucket
    for r in speed_rows("run-moe", "model-arch2", exp=2):
        store.add_run(r)

    body = client.get("/api/ranking").json()
    archs = {(m["model_version"], m["architecture"]) for m in body["models"]}
    # L0 identity is model_key -> "model-arch"; no num_experts resolves to unknown.
    assert ("model-arch", "unknown") in archs
    # L0 identity is model_key -> "model-arch2"; num_experts=2 resolves to moe.
    assert ("model-arch2", "moe") in archs


# ---------------------------------------------------------------------------
# Integrity path: one bad Workflow artifact is skipped, never aborting the surface.
# ---------------------------------------------------------------------------

def test_invalid_workflow_artifact_is_skipped_not_fatal(store, speed_rows):
    for r in speed_rows("run-speed-4", "model-x"):
        store.add_run(r)
    # A deliberately malformed artifact (wrong schema_version).
    _persist_workflow("run-bad", "model-x", 10, 20, fingerprint="fp-bad")

    resp = client.get("/api/ranking")
    # Surface still builds: Speed evidence ranks; the bad Workflow artifact is excluded.
    assert resp.status_code == 200
    body = resp.json()
    dim = body["models"][0]["dimensions"]
    assert dim[DIMENSION_SPEED]["status"] == "available"
    # Agentic dimension has no *valid* view for this model -> not available.
    assert dim[DIMENSION_AGENtic]["status"] in ("unavailable",)


def test_load_agentic_views_skips_non_json_files(tmp_path, monkeypatch):
    monkeypatch.setattr(art, "V2_RUNS_DIR", tmp_path)
    # A stray .log file must be ignored by the reader (never attempted as an artifact).
    (tmp_path / "run-1.log").write_text("")
    assert _load_agentic_views() == []


# ---------------------------------------------------------------------------
# Backward compatibility: legacy single-row speed row still readable.
# ---------------------------------------------------------------------------

def test_legacy_single_row_speed_row_ranks(store, speed_rows):
    # A single flat Standard Speed row (legacy shape) must still read through the store.
    one = _speed_row("run-legacy", "model-legacy", 8192, None, stage="cold")
    store.add_run(one)

    body = client.get("/api/ranking").json()
    versions = {m["model_version"] for m in body["models"]}
    assert "model-legacy" in versions


# ---------------------------------------------------------------------------
# Route hygiene: no benchmark execution / legacy executor import.
# ---------------------------------------------------------------------------

def test_route_does_not_import_or_call_legacy_executor():
    import sys
    before = set(sys.modules)
    client.get("/api/ranking")
    after = set(sys.modules)
    # The route must not pull in the retired V2 quality executor graph at runtime.
    leaked = {m for m in (after - before) if "v2_quality_executor" in m}
    assert not leaked
