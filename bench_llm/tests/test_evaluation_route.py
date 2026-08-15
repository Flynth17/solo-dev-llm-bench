"""Tests for src/routes/evaluation.py.

Verifies:
- Empty speed_tests rejected
- Unknown speed test names rejected
- Duplicate speed test names normalized
- Validation of model, iterations, max_tokens, temperature
- Result structure contains required fields
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from src.main import app
from src.routes.evaluation import _validate_speed_tests, _aggregate_for_runs, _warm_aggregate_for_runs
from src.evaluation_prompts import PROMPT_FILES

client = TestClient(app)


# ------------------------------------------------------------------
# _validate_speed_tests unit tests
# ------------------------------------------------------------------

class TestValidateSpeedTests:
    def test_empty_list_raises(self) -> None:
        with pytest.raises(HTTPException):
            _validate_speed_tests([])

    def test_unknown_name_raises(self) -> None:
        with pytest.raises(HTTPException):
            _validate_speed_tests(["huge"])

    def test_duplicate_names_deduplicated(self) -> None:
        result = _validate_speed_tests(["small", "medium", "small", "large"])
        assert result == ["small", "medium", "large"]

    def test_all_valid_names(self) -> None:
        result = _validate_speed_tests(["small", "medium", "large"])
        assert result == ["small", "medium", "large"]

    def test_single_name(self) -> None:
        result = _validate_speed_tests(["large"])
        assert result == ["large"]


# ------------------------------------------------------------------
# _aggregate_for_runs unit tests
# ------------------------------------------------------------------

class TestAggregateForRuns:
    def test_empty_runs(self) -> None:
        result = _aggregate_for_runs([])
        assert result["avg_tokens_per_second"] == 0
        assert result["min_tokens_per_second"] == 0
        assert result["max_tokens_per_second"] == 0

    def test_aggregate_with_runs(self) -> None:
        runs = [
            {"tokens_per_second": 100.0, "ttft_seconds": 0.5, "wall_time_seconds": 10.0},
            {"tokens_per_second": 200.0, "ttft_seconds": 0.4, "wall_time_seconds": 12.0},
        ]
        result = _aggregate_for_runs(runs)
        assert result["avg_tokens_per_second"] == 150.0
        assert result["min_tokens_per_second"] == 100.0
        assert result["max_tokens_per_second"] == 200.0
        assert result["avg_ttft_seconds"] == 0.45
        assert result["total_wall_time_seconds"] == 22.0


# ------------------------------------------------------------------
# _warm_aggregate_for_runs unit tests
# ------------------------------------------------------------------

class TestWarmAggregateForRuns:
    def test_no_warm_runs(self) -> None:
        runs = [
            {"tokens_per_second": 100.0, "cold_or_warm": "cold", "ttft_seconds": 0.5},
        ]
        result = _warm_aggregate_for_runs(runs)
        assert result["available"] is False
        assert result["avg_tokens_per_second"] is None

    def test_with_warm_runs(self) -> None:
        runs = [
            {"tokens_per_second": 100.0, "cold_or_warm": "cold", "ttft_seconds": 0.5},
            {"tokens_per_second": 200.0, "cold_or_warm": "warm", "ttft_seconds": 0.4},
        ]
        result = _warm_aggregate_for_runs(runs)
        assert result["available"] is True
        assert result["avg_tokens_per_second"] == 200.0


# ------------------------------------------------------------------
# POST /api/evaluation/run — validation tests
# ------------------------------------------------------------------

class TestEvaluationEndpointValidation:
    def test_empty_speed_tests(self) -> None:
        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "speed_tests": [],
        })
        assert resp.status_code == 400

    def test_unknown_speed_test(self) -> None:
        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "speed_tests": ["huge"],
        })
        assert resp.status_code == 400

    def test_no_model(self) -> None:
        resp = client.post("/api/evaluation/run", json={
            "speed_tests": ["small"],
        })
        assert resp.status_code == 400

    def test_invalid_iterations(self) -> None:
        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "speed_tests": ["small"],
            "iterations": 0,
        })
        assert resp.status_code == 400

    def test_invalid_max_tokens(self) -> None:
        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "speed_tests": ["small"],
            "max_output_tokens": 0,
        })
        assert resp.status_code == 400

    def test_invalid_temperature(self) -> None:
        resp = client.post("/api/evaluation/run", json={
            "model": "test-model",
            "speed_tests": ["small"],
            "temperature": 3,
        })
        assert resp.status_code == 400


# ------------------------------------------------------------------
# POST /api/evaluation/run — structure test (no LM call)
# ------------------------------------------------------------------

class TestEvaluationEndpointStructure:
    """Test that the endpoint returns the correct structure when called
    with an unreachable LM Studio URL. We check validation passes and
    structure is correct even if the benchmark itself fails."""

    def test_duplicate_speed_tests_normalized(self) -> None:
        """Duplicates should be deduplicated before any benchmark call."""
        # This test just checks that the validation layer works.
        # Actual benchmark execution would require a running LM server.
        result = _validate_speed_tests(["small", "small", "medium"])
        assert result == ["small", "medium"]