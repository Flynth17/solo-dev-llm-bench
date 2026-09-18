"""Tests for the Context read model (projection + integrity).

Verifies the read model surfaces authoritative per-point data verbatim and fails
explicitly on inconsistent artifacts rather than "repairing" them -- so a consumer
never recomputes or is misled.
"""

from __future__ import annotations

import pytest

import src.v2_context_artifact as art
import src.v2_context_read_model as rm


def _point(req, status="success", score=1.0):
    return {
        "context_point": f"{req}K" if req < 1000 else f"{req//1000}K",
        "requested_context_tokens": req,
        "actual_context_tokens": req,
        "status": status,
        "score": score,
        "facts_requested": 12,
        "facts_correct": 0 if score is None else int(round(score * 12)),
        "baseline": False,
        "degradation_from_baseline": None,
        "retention_relative_to_baseline": None,
        "failure_reason": None,
        "evidence": [],
        "telemetry": {},
    }


def _document(points, baseline_point=None):
    return {
        "schema_version": art.SCHEMA_VERSION,
        "artifact_type": art.ARTIFACT_TYPE,
        "context_run_id": "ctx-abc",
        "model_key": "m",
        "configuration_fingerprint": "fp",
        "classification": "canonical",
        "baseline_context_point": baseline_point,
        "effective_capacity": 131072,
        "points": points,
    }


def test_build_read_model_projects_verbatim():
    doc = _document([_point(2000, score=1.0), _point(8000, score=0.833333)])
    view = rm.build_read_model(doc)
    assert view["run_id"] == "ctx-abc"
    assert view["supported_point_count"] == 2
    assert view["gap_point_count"] == 0
    # ordered ascending by requested tokens
    reqs = [p["requested_context_tokens"] for p in view["points"]]
    assert reqs == sorted(reqs)
    # scores surfaced verbatim, not recomputed
    assert view["points"][1]["score"] == 0.833333


def test_gaps_counted_and_preserved():
    doc = _document([_point(2000), _point(8000, status="unsupported", score=None)])
    view = rm.build_read_model(doc)
    assert view["supported_point_count"] == 1
    assert view["gap_point_count"] == 1
    gap = view["points"][1]
    assert gap["status"] == "unsupported"
    assert gap["score"] is None  # gap preserved, not zeroed


def test_baseline_must_match_a_persisted_point():
    doc = _document([_point(2000), _point(8000)], baseline_point="999K")
    with pytest.raises(rm.ContextReadModelIntegrityError):
        rm.build_read_model(doc)


def test_missing_configuration_fingerprint_fails():
    doc = _document([_point(2000)])
    del doc["configuration_fingerprint"]
    with pytest.raises(rm.ContextReadModelIntegrityError):
        rm.build_read_model(doc)


def test_wrong_schema_version_fails():
    doc = _document([_point(2000)])
    doc["schema_version"] = art.SCHEMA_VERSION + 999
    with pytest.raises(rm.ContextArtifactSchemaError):
        rm.build_read_model(doc)


def test_empty_points_fails():
    with pytest.raises(rm.ContextArtifactSchemaError):
        rm.build_read_model(_document([]))


def test_load_unknown_run_raises_not_found():
    with pytest.raises(rm.ContextRunNotFoundError):
        rm.load_context_result("ctx-does-not-exist")
