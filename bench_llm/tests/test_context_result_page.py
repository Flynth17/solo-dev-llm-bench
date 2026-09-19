"""Context single-run result page + read-model contract (RM-26-AA-0018).

Covers the two frontend features added for this Act:

* ST-006 point coverage rollup -- supported / unsupported / missing / invalid /
  failed are categorised from AUTHORITATIVE per-point status, so an absent
  contract point (missing) is never conflated with a capacity-exceeded one
  (unsupported). The read model must expose ``context_points_contract`` for this.
* ST-007 L2 evidence drill-down -- every measured point's ``evidence`` is
  surfaced verbatim ({key, expected, actual, passed}) so the UI can trace a
  plotted point back to its facts without re-deriving anything.

It also asserts the served HTML page mounts the persistent sidebar and loads the
context-result assets. Persistence goes to a throwaway tmp dir (monkeypatched),
so no real data side effects occur. No model runtime is spawned.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.main  # noqa: E401  (imports the FastAPI app)
import src.v2_context_artifact as art

_PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = _PROJECT_ROOT / "static"
client = TestClient(src.main.app)  # noqa: E402


def _served(path):
    return client.get(path).text


def _persist_multi_state(run_id):
    """Write a schema-valid multi-state artifact (success/unsupported/malformed)."""
    doc = {
        "schema_version": art.SCHEMA_VERSION,
        "artifact_type": art.ARTIFACT_TYPE,
        "context_run_id": run_id,
        "model_key": "test-model-1",
        "model_display_name": "Test Model 1",
        "configuration_fingerprint": "fp-st009",
        "classification": "context_degradation",
        "baseline_context_point": "15K",
        "effective_capacity": 60000,
        "context_points_contract": [15000, 30000, 60000, 120000],
        "points": [
            {
                "context_point": "15K",
                "requested_context_tokens": 15000,
                "actual_context_tokens": 14800,
                "status": "success",
                "score": 1.0,
                "facts_requested": 2,
                "facts_correct": 2,
                "baseline": True,
                "degradation_from_baseline": 0.0,
                "retention_relative_to_baseline": 1.0,
                "failure_reason": None,
                "evidence": [
                    {"key": "city", "expected": "Paris", "actual": "Paris", "passed": True},
                    {"key": "pop", "expected": "2.1M", "actual": "2.1M", "passed": True},
                ],
                "telemetry": {"input_tokens": 14800},
            },
            {
                "context_point": "30K",
                "requested_context_tokens": 30000,
                "actual_context_tokens": 29500,
                "status": "success",
                "score": 0.5,
                "facts_requested": 2,
                "facts_correct": 1,
                "baseline": False,
                "degradation_from_baseline": -0.5,
                "retention_relative_to_baseline": 0.5,
                "failure_reason": None,
                "evidence": [
                    {"key": "city", "expected": "Paris", "actual": "London", "passed": False},
                ],
                "telemetry": {"input_tokens": 29500},
            },
            {
                "context_point": "60K",
                "requested_context_tokens": 60000,
                "actual_context_tokens": None,
                "status": "unsupported",
                "score": None,
                "facts_requested": 2,
                "facts_correct": 0,
                "baseline": False,
                "degradation_from_baseline": None,
                "retention_relative_to_baseline": None,
                "failure_reason": "capacity exceeded",
                "evidence": [],
                "telemetry": {},
            },
            # 120K is in the contract but has NO record -> missing (distinct from unsupported).
        ],
    }
    art.persist_document(doc)
    return run_id


@pytest.fixture(autouse=True)
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(art, "CONTEXT_RUNS_DIR", tmp_path)
    yield tmp_path


# ---------------------------------------------------------------------------
# Page serving + persistent shell
# ---------------------------------------------------------------------------

def test_context_result_page_serves_with_persistent_sidebar():
    body = _served("/context/results/ctx-st009-page")
    assert "app-sidebar-mount" in body
    assert "/static/app-shell.js" in body
    assert "/static/context-result.css" in body


def test_context_result_page_marks_evidence_and_coverage_markup():
    """The served page exposes the ST-006 coverage section and ST-007 evidence column."""
    body = _served("/context/results/ctx-st009-page")
    assert "id=\"cr-coverage\"" in body, "missing point-coverage rollup section"
    assert "id=\"cr-coverage-body\"" in body
    # Evidence is a real table column header on the per-point table.
    assert re.search(r"<th[^>]*>Evidence</th>", body), "missing Evidence column header"


def test_context_result_assets_define_st006_and_st007_logic():
    """The served JS actually contains the coverage + evidence drill-down code."""
    js = (STATIC_DIR / "context-result.js").read_text(encoding="utf-8")
    # ST-006: coverage categorisation exists.
    assert "cr-cov-supported" in js and "cr-cov-missing" in js
    # ST-007: evidence drill-down table + verbatim fact rendering exist.
    assert "cr-ev-table" in js and "cr-ev-resultlabel" in js


# ---------------------------------------------------------------------------
# Read-model contract the frontend depends on
# ---------------------------------------------------------------------------

def test_read_model_surfaces_contract_and_counts_for_coverage():
    rid = _persist_multi_state("ctx-st009-coverage")
    body = client.get(f"/api/context/runs/{rid}").json()
    # context_points_contract is the coverage input (4 contract points).
    assert body["context_points_contract"] == [15000, 30000, 60000, 120000]
    # Backend counts only records that exist: two supported (success), one gap
    # (the unsupported 60K). The absent 120K is NOT a backend count -- it is the
    # "missing" category the ST-006 coverage rollup derives from the contract vs
    # points delta, keeping missing distinct from unsupported.
    assert body["supported_point_count"] == 2
    assert body["gap_point_count"] == 1
    # Contract declares 4 points but only 3 records exist -- the one missing
    # contract point is what the ST-006 coverage rollup reports as "missing",
    # distinct from the unsupported gap above.
    assert len(body["context_points_contract"]) == 4
    assert len(body["points"]) == 3


def test_read_model_surfaces_evidence_verbatim_for_drill_down():
    rid = _persist_multi_state("ctx-st009-evidence")
    body = client.get(f"/api/context/runs/{rid}").json()
    by_point = {p["context_point"]: p for p in body["points"]}
    # Measured points carry their facts verbatim (key/expected/actual/passed).
    city = next(e for e in by_point["15K"]["evidence"] if e["key"] == "city")
    assert city == {"key": "city", "expected": "Paris", "actual": "Paris", "passed": True}
    # Telemetry is present so the L2 drill-down can show what was actually run.
    assert by_point["15K"]["telemetry"]["input_tokens"] == 14800


def test_unsupported_point_is_na_not_zero():
    """N/A must never be coerced to zero -- unsupported points keep null score."""
    rid = _persist_multi_state("ctx-st009-na")
    body = client.get(f"/api/context/runs/{rid}").json()
    by_point = {p["context_point"]: p for p in body["points"]}
    assert by_point["60K"]["status"] == "unsupported"
    assert by_point["60K"]["score"] is None
    assert by_point["60K"]["retention_relative_to_baseline"] is None
