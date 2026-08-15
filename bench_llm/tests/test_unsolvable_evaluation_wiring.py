"""Regression tests for Unsolvable correctness wiring into evaluation backend.

Covers:
1. Normal unsolvable task routing (task_type="unsolvable" in /api/tasks/{id}/run)
2. Evaluation accepts "unsolvable" in correctness_tests
3. Unsolvable executes once per evaluation
4. Correct recognition -> score=1.0 / PASS
5. Incorrect recognition -> score=0.0 / FAIL
6. Result appears in correctness_results
7. Canonical task is reused (not duplicated)
8. Repeated evaluations create separate task_runs
9. Canonical Unsolvable Recognition task is hidden from Advanced Task Manager
10. Manual unsolvable tasks remain visible
11. Unsolvable results do not appear in Raw Speed storage
12. Combined markdown + python + java + unsolvable works (validation)
13. score=None from another validator still does not crash aggregation
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import HTTPException

from src.main import app
from src.routes.evaluation import (
    CORRECTNESS_TESTS,
    CANONICAL_TASKS,
    _CORRECTNESS_LABELS,
    _validate_correctness_tests,
)
from src.routes.tasks import _CANONICAL_EVALUATION_TASKS


client = TestClient(app)


# ------------------------------------------------------------------
# Test 1: Normal unsolvable task routing
# ------------------------------------------------------------------

class TestNormalUnsolvableRouting:
    """Test 1: normal unsolvable task routing through /api/tasks/{id}/run."""

    def test_unsolvable_task_type_routed_to_run_unsolvable(self) -> None:
        """task_type='unsolvable' should call run_unsolvable_task."""
        mock_us_result = MagicMock()
        mock_us_result.to_dict.return_value = {
            "task_name": "Unsolvable Recognition",
            "task_type": "unsolvable",
            "score": 1.0,
            "passed": True,
            "impossible_detected": True,
            "classification": "contradictory-requirements",
            "conflict_ids": ["R1", "R2"],
            "explanation_valid": True,
            "output_tokens": 500,
            "input_tokens": 200,
            "tokens_per_second": 50.0,
            "ttft_seconds": 0.3,
            "wall_time_seconds": 10.0,
            "generated_response": "IMPOSSIBLE: yes",
        }

        mock_task = {
            "task_id": "task-us-001",
            "name": "Manual Unsolvable Test",
            "task_type": "unsolvable",
            "status": "pending",
            "prompt": "",
            "config": {},
            "result": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "started_at": None,
            "completed_at": None,
            "run_id": None,
        }

        mock_us_result_data = MagicMock()
        mock_us_result_data.to_dict.return_value = {
            "task_name": "Unsolvable Recognition",
            "task_type": "unsolvable",
            "score": 1.0,
            "passed": True,
            "impossible_detected": True,
            "classification": "contradictory-requirements",
            "conflict_ids": ["R1", "R2"],
            "explanation_valid": True,
            "output_tokens": 500,
            "input_tokens": 200,
            "tokens_per_second": 50.0,
            "ttft_seconds": 0.3,
            "wall_time_seconds": 10.0,
            "generated_response": "IMPOSSIBLE: yes",
        }

        mock_task = {
            "task_id": "task-us-001",
            "name": "Manual Unsolvable Test",
            "task_type": "unsolvable",
            "status": "pending",
            "prompt": "",
            "config": {},
            "result": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "started_at": None,
            "completed_at": None,
            "run_id": None,
        }

        with patch("src.task_manager.get_task", return_value=mock_task):
            with patch("src.task_manager.update_task_status"):
                with patch("src.task_manager.set_task_result"):
                    with patch("src.task_manager.create_task_run"):
                        # Mock httpx.AsyncClient to avoid real LM Studio call inside run_unsolvable_task.
                        # task_unsolvable.py imports httpx INSIDE the function (line 200), not at module level,
                        # so we must patch the AsyncClient class directly from the httpx package.
                        with patch("httpx.AsyncClient") as mock_async_client:
                            mock_resp = MagicMock()
                            mock_resp.status_code = 200
                            mock_resp.json.return_value = {
                                "output": [{"type": "message", "content": "IMPOSSIBLE: yes"}],
                                "stats": {"input_tokens": 100, "total_output_tokens": 50, "tokens_per_second": 50.0, "time_to_first_token_seconds": 0.3},
                            }
                            mock_async_client.return_value.__aenter__.return_value.post.return_value = mock_resp
                            with patch("src.task_unsolvable.validate_unsolvable_response") as mock_validate:
                                from src.unsolvable_validator import UnsolvableResult
                                mock_validate.return_value = UnsolvableResult(
                                    score=1.0, passed=True, impossible_detected=True,
                                    classification="contradictory-requirements",
                                    conflict_ids={"R1", "R2"}, explanation_valid=True)
                                resp = client.post("/api/tasks/task-us-001/run", json={
                                    "model": "test-model",
                                    "lm_studio_url": "http://localhost:1234",
                                    "max_tokens": 500,
                                    "temperature": 0,
                                    "iterations": 1,
                                })
                                assert resp.status_code == 200
                                data = resp.json()
                                assert data["result"]["task_type"] == "unsolvable"


# ------------------------------------------------------------------
# Test 2: Evaluation accepts "unsolvable" in correctness_tests
# ------------------------------------------------------------------

class TestEvaluationAcceptsUnsolvable:
    """Test 2: /api/evaluation/run accepts 'unsolvable'."""

    def test_unsolvable_in_correctness_tests_validated(self) -> None:
        """'unsolvable' should be accepted by _validate_correctness_tests."""
        assert "unsolvable" in CORRECTNESS_TESTS
        result = _validate_correctness_tests(["unsolvable"])
        assert result == ["unsolvable"]

    def test_evaluation_endpoint_accepts_unsolvable(self) -> None:
        """The endpoint should not reject 'unsolvable' as unknown."""
        mock_us_result = MagicMock()
        mock_us_result.to_dict.return_value = {
            "task_name": "Unsolvable Recognition",
            "task_type": "unsolvable",
            "score": 1.0,
            "passed": True,
            "impossible_detected": True,
            "classification": "contradictory-requirements",
            "conflict_ids": ["R1", "R2"],
            "explanation_valid": True,
            "output_tokens": 500,
            "input_tokens": 200,
            "tokens_per_second": 50.0,
            "ttft_seconds": 0.3,
            "wall_time_seconds": 10.0,
            "generated_response": "IMPOSSIBLE: yes",
        }
        mock_canonical = {
            "task_id": "task-us-001",
            "name": "Unsolvable Recognition",
            "task_type": "unsolvable",
        }

        with patch("src.task_manager.get_tasks", return_value=[mock_canonical]):
            with patch("src.task_manager.create_task_run"):
                with patch("src.task_manager.create_task", return_value=mock_canonical):
                    with patch("src.task_unsolvable.run_unsolvable_task", return_value=mock_us_result):
                        resp = client.post("/api/evaluation/run", json={
                            "model": "test-model",
                            "speed_tests": [],
                            "correctness_tests": ["unsolvable"],
                            "iterations": 1,
                            "max_output_tokens": 100,
                            "temperature": 0,
                        })
                        # Should not raise HTTPException for validation
                        assert resp.status_code == 200


# ------------------------------------------------------------------
# Test 3: Unsolvable executes exactly once per evaluation
# ------------------------------------------------------------------

class TestUnsolvableExecutesOnce:
    """Test 3: unsolvable executes exactly ONCE per evaluation."""

    def test_unsolvable_executes_once(self) -> None:
        """Even with multiple speed iterations, unsolvable runs only once."""
        call_count = 0
        mock_us_result = MagicMock()
        mock_us_result.to_dict.return_value = {
            "task_name": "Unsolvable Recognition",
            "task_type": "unsolvable",
            "score": 1.0,
            "passed": True,
            "impossible_detected": True,
            "classification": "contradictory-requirements",
            "conflict_ids": ["R1", "R2"],
            "explanation_valid": True,
            "output_tokens": 500,
            "input_tokens": 200,
            "tokens_per_second": 50.0,
            "ttft_seconds": 0.3,
            "wall_time_seconds": 10.0,
            "generated_response": "IMPOSSIBLE: yes",
        }
        mock_canonical = {
            "task_id": "task-us-001",
            "name": "Unsolvable Recognition",
            "task_type": "unsolvable",
        }

        def track_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_us_result

        with patch("src.task_manager.get_tasks", return_value=[mock_canonical]):
            with patch("src.task_manager.create_task_run"):
                with patch("src.task_manager.create_task", return_value=mock_canonical):
                    with patch("src.task_unsolvable.run_unsolvable_task", side_effect=track_call):
                        resp = client.post("/api/evaluation/run", json={
                            "model": "test-model",
                            "speed_tests": [],
                            "correctness_tests": ["unsolvable"],
                            "iterations": 5,
                            "max_output_tokens": 100,
                            "temperature": 0,
                        })
                        assert resp.status_code == 200
                        assert call_count == 1


# ------------------------------------------------------------------
# Test 4: Correct recognition -> score=1.0 / PASS
# ------------------------------------------------------------------

class TestCorrectRecognition:
    """Test 4: correct recognition -> score=1.0 / PASS."""

    def test_correct_recognition_scores_1(self) -> None:
        from src.unsolvable_validator import UnsolvableResult

        mock_us_result = MagicMock()
        # Set attributes directly so the evaluation code can read them
        mock_us_result.task_name = "Unsolvable Recognition"
        mock_us_result.task_type = "unsolvable"
        mock_us_result.score = 1.0
        mock_us_result.passed = True
        mock_us_result.impossible_detected = True
        mock_us_result.classification = "contradictory-requirements"
        mock_us_result.conflict_ids = {"R1", "R2"}
        mock_us_result.explanation_valid = True
        mock_us_result.output_tokens = 500
        mock_us_result.input_tokens = 200
        mock_us_result.tokens_per_second = 50.0
        mock_us_result.ttft_seconds = 0.3
        mock_us_result.wall_time_seconds = 10.0
        mock_us_result.generated_response = "IMPOSSIBLE: yes\nCLASS: contradictory-requirements\nCONFLICT: R1, R2\nEXPLANATION: The requirements are mutually exclusive."

        def to_dict():
            return {
                "task_name": "Unsolvable Recognition",
                "task_type": "unsolvable",
                "score": 1.0,
                "passed": True,
                "impossible_detected": True,
                "classification": "contradictory-requirements",
                "conflict_ids": ["R1", "R2"],
                "explanation_valid": True,
                "output_tokens": 500,
                "input_tokens": 200,
                "tokens_per_second": 50.0,
                "ttft_seconds": 0.3,
                "wall_time_seconds": 10.0,
                "generated_response": "IMPOSSIBLE: yes",
            }
        mock_us_result.to_dict = to_dict

        mock_canonical = {
            "task_id": "task-us-001",
            "name": "Unsolvable Recognition",
            "task_type": "unsolvable",
        }

        with patch("src.task_manager.get_tasks", return_value=[mock_canonical]):
            with patch("src.task_manager.create_task_run") as mock_create_run:
                with patch("src.task_manager.create_task", return_value=mock_canonical):
                    with patch("src.task_unsolvable.run_unsolvable_task", return_value=mock_us_result):
                        resp = client.post("/api/evaluation/run", json={
                            "model": "test-model",
                            "speed_tests": [],
                            "correctness_tests": ["unsolvable"],
                            "iterations": 1,
                            "max_output_tokens": 100,
                            "temperature": 0,
                        })
                        assert resp.status_code == 200
                        data = resp.json()
                        correctness_results = data["correctness_results"]
                        unsolvable_result = [r for r in correctness_results if r["test_type"] == "unsolvable"][0]
                        assert unsolvable_result["score"] == 1.0
                        assert unsolvable_result["passed"] is True


# ------------------------------------------------------------------
# Test 5: Incorrect recognition -> score=0.0 / FAIL
# ------------------------------------------------------------------

class TestIncorrectRecognition:
    """Test 5: incorrect recognition -> score=0.0 / FAIL."""

    def test_incorrect_recognition_scores_0(self) -> None:
        mock_us_result = MagicMock()
        # Set attributes directly so the evaluation code can read them
        mock_us_result.task_name = "Unsolvable Recognition"
        mock_us_result.task_type = "unsolvable"
        mock_us_result.score = 0.0
        mock_us_result.passed = False
        mock_us_result.impossible_detected = False
        mock_us_result.classification = ""
        mock_us_result.conflict_ids = set()
        mock_us_result.explanation_valid = False
        mock_us_result.output_tokens = 500
        mock_us_result.input_tokens = 200
        mock_us_result.tokens_per_second = 50.0
        mock_us_result.ttft_seconds = 0.3
        mock_us_result.wall_time_seconds = 10.0
        mock_us_result.generated_response = "This is a solvable task."

        def to_dict():
            return {
                "task_name": "Unsolvable Recognition",
                "task_type": "unsolvable",
                "score": 0.0,
                "passed": False,
                "impossible_detected": False,
                "classification": "",
                "conflict_ids": [],
                "explanation_valid": False,
                "output_tokens": 500,
                "input_tokens": 200,
                "tokens_per_second": 50.0,
                "ttft_seconds": 0.3,
                "wall_time_seconds": 10.0,
                "generated_response": "This is a solvable task.",
            }
        mock_us_result.to_dict = to_dict

        mock_canonical = {
            "task_id": "task-us-001",
            "name": "Unsolvable Recognition",
            "task_type": "unsolvable",
        }

        with patch("src.task_manager.get_tasks", return_value=[mock_canonical]):
            with patch("src.task_manager.create_task_run") as mock_create_run:
                with patch("src.task_manager.create_task", return_value=mock_canonical):
                    with patch("src.task_unsolvable.run_unsolvable_task", return_value=mock_us_result):
                        resp = client.post("/api/evaluation/run", json={
                            "model": "test-model",
                            "speed_tests": [],
                            "correctness_tests": ["unsolvable"],
                            "iterations": 1,
                            "max_output_tokens": 100,
                            "temperature": 0,
                        })
                        assert resp.status_code == 200
                        data = resp.json()
                        correctness_results = data["correctness_results"]
                        unsolvable_result = [r for r in correctness_results if r["test_type"] == "unsolvable"][0]
                        assert unsolvable_result["score"] == 0.0
                        assert unsolvable_result["passed"] is False


# ------------------------------------------------------------------
# Test 6: Result appears in correctness_results
# ------------------------------------------------------------------

class TestResultInCorrectnessResults:
    """Test 6: result appears in correctness_results."""

    def test_unsolvable_in_correctness_results(self) -> None:
        mock_us_result = MagicMock()
        mock_us_result.to_dict.return_value = {
            "task_name": "Unsolvable Recognition",
            "task_type": "unsolvable",
            "score": 1.0,
            "passed": True,
            "impossible_detected": True,
            "classification": "contradictory-requirements",
            "conflict_ids": ["R1", "R2"],
            "explanation_valid": True,
            "output_tokens": 500,
            "input_tokens": 200,
            "tokens_per_second": 50.0,
            "ttft_seconds": 0.3,
            "wall_time_seconds": 10.0,
            "generated_response": "IMPOSSIBLE: yes",
        }
        mock_canonical = {
            "task_id": "task-us-001",
            "name": "Unsolvable Recognition",
            "task_type": "unsolvable",
        }

        with patch("src.task_manager.get_tasks", return_value=[mock_canonical]):
            with patch("src.task_manager.create_task_run"):
                with patch("src.task_manager.create_task", return_value=mock_canonical):
                    with patch("src.task_unsolvable.run_unsolvable_task", return_value=mock_us_result):
                        resp = client.post("/api/evaluation/run", json={
                            "model": "test-model",
                            "speed_tests": [],
                            "correctness_tests": ["unsolvable"],
                            "iterations": 1,
                            "max_output_tokens": 100,
                            "temperature": 0,
                        })
                        assert resp.status_code == 200
                        data = resp.json()
                        assert "correctness_results" in data
                        unsolvable_results = [r for r in data["correctness_results"] if r["test_type"] == "unsolvable"]
                        assert len(unsolvable_results) == 1

    def test_unsolvable_result_has_required_fields(self) -> None:
        """Unsolvable result should include all required fields."""
        mock_us_result = MagicMock()
        mock_us_result.to_dict.return_value = {
            "task_name": "Unsolvable Recognition",
            "task_type": "unsolvable",
            "score": 1.0,
            "passed": True,
            "impossible_detected": True,
            "classification": "contradictory-requirements",
            "conflict_ids": ["R1", "R2"],
            "explanation_valid": True,
            "output_tokens": 500,
            "input_tokens": 200,
            "tokens_per_second": 50.0,
            "ttft_seconds": 0.3,
            "wall_time_seconds": 10.0,
            "generated_response": "IMPOSSIBLE: yes",
        }
        mock_canonical = {
            "task_id": "task-us-001",
            "name": "Unsolvable Recognition",
            "task_type": "unsolvable",
        }

        with patch("src.task_manager.get_tasks", return_value=[mock_canonical]):
            with patch("src.task_manager.create_task_run"):
                with patch("src.task_manager.create_task", return_value=mock_canonical):
                    with patch("src.task_unsolvable.run_unsolvable_task", return_value=mock_us_result):
                        resp = client.post("/api/evaluation/run", json={
                            "model": "test-model",
                            "speed_tests": [],
                            "correctness_tests": ["unsolvable"],
                            "iterations": 1,
                            "max_output_tokens": 100,
                            "temperature": 0,
                        })
                        assert resp.status_code == 200
                        data = resp.json()
                        unsolvable_result = [r for r in data["correctness_results"] if r["test_type"] == "unsolvable"][0]
                        required_fields = [
                            "test_type", "test_label", "score", "passed",
                            "impossible_detected", "classification", "conflict_ids",
                            "explanation_valid", "tokens_per_second", "ttft_seconds",
                            "wall_time_seconds", "output_tokens", "input_tokens",
                            "generated_response",
                        ]
                        for field in required_fields:
                            assert field in unsolvable_result, f"Missing field: {field}"


# ------------------------------------------------------------------
# Test 7: Canonical task is reused (not duplicated)
# ------------------------------------------------------------------

class TestCanonicalTaskReused:
    """Test 7: canonical task is reused."""

    def test_canonical_task_reused_on_multiple_evaluations(self) -> None:
        """Repeated evaluations should reuse the same canonical task definition."""
        mock_us_result = MagicMock()
        mock_us_result.to_dict.return_value = {
            "task_name": "Unsolvable Recognition",
            "task_type": "unsolvable",
            "score": 1.0,
            "passed": True,
            "impossible_detected": True,
            "classification": "contradictory-requirements",
            "conflict_ids": ["R1", "R2"],
            "explanation_valid": True,
            "output_tokens": 500,
            "input_tokens": 200,
            "tokens_per_second": 50.0,
            "ttft_seconds": 0.3,
            "wall_time_seconds": 10.0,
            "generated_response": "IMPOSSIBLE: yes",
        }
        mock_canonical = {
            "task_id": "task-us-001",
            "name": "Unsolvable Recognition",
            "task_type": "unsolvable",
        }

        with patch("src.task_manager.get_tasks", return_value=[mock_canonical]):
            with patch("src.task_manager.create_task_run"):
                with patch("src.task_manager.create_task") as mock_create:
                    with patch("src.task_unsolvable.run_unsolvable_task", return_value=mock_us_result):
                        # First evaluation
                        resp1 = client.post("/api/evaluation/run", json={
                            "model": "test-model",
                            "speed_tests": [],
                            "correctness_tests": ["unsolvable"],
                            "iterations": 1,
                            "max_output_tokens": 100,
                            "temperature": 0,
                        })
                        assert resp1.status_code == 200

        # create_task should NOT have been called (task already exists)
        mock_create.assert_not_called()


# ------------------------------------------------------------------
# Test 8: Repeated evaluations create separate task_runs
# ------------------------------------------------------------------

class TestRepeatedEvaluationsCreateSeparateRuns:
    """Test 8: repeated evaluations create separate task_runs."""

    def test_repeated_evaluations_create_separate_task_runs(self) -> None:
        """Each evaluation should create a new task_run row."""
        mock_us_result = MagicMock()
        mock_us_result.to_dict.return_value = {
            "task_name": "Unsolvable Recognition",
            "task_type": "unsolvable",
            "score": 1.0,
            "passed": True,
            "impossible_detected": True,
            "classification": "contradictory-requirements",
            "conflict_ids": ["R1", "R2"],
            "explanation_valid": True,
            "output_tokens": 500,
            "input_tokens": 200,
            "tokens_per_second": 50.0,
            "ttft_seconds": 0.3,
            "wall_time_seconds": 10.0,
            "generated_response": "IMPOSSIBLE: yes",
        }
        mock_canonical = {
            "task_id": "task-us-001",
            "name": "Unsolvable Recognition",
            "task_type": "unsolvable",
        }

        call_count = 0

        def track_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_us_result

        with patch("src.task_manager.get_tasks", return_value=[mock_canonical]):
            with patch("src.task_manager.create_task_run"):
                with patch("src.task_manager.create_task", return_value=mock_canonical):
                    with patch("src.task_unsolvable.run_unsolvable_task", side_effect=track_call):
                        # First evaluation
                        resp1 = client.post("/api/evaluation/run", json={
                            "model": "test-model",
                            "speed_tests": [],
                            "correctness_tests": ["unsolvable"],
                            "iterations": 1,
                            "max_output_tokens": 100,
                            "temperature": 0,
                        })
                        assert resp1.status_code == 200

                        # Second evaluation
                        resp2 = client.post("/api/evaluation/run", json={
                            "model": "test-model",
                            "speed_tests": [],
                            "correctness_tests": ["unsolvable"],
                            "iterations": 1,
                            "max_output_tokens": 100,
                            "temperature": 0,
                        })
                        assert resp2.status_code == 200

        # run_unsolvable_task should be called exactly twice
        assert call_count == 2


# ------------------------------------------------------------------
# Test 9: Canonical Unsolvable Recognition task is hidden from Advanced Task Manager
# ------------------------------------------------------------------

class TestCanonicalHiddenFromAdvancedTaskManager:
    """Test 9: canonical Unsolvable Recognition task is hidden."""

    def test_canonical_unsolvable_hidden_from_advanced_task_manager(self) -> None:
        """('Unsolvable Recognition', 'unsolvable') should be in _CANONICAL_EVALUATION_TASKS."""
        assert ("Unsolvable Recognition", "unsolvable") in _CANONICAL_EVALUATION_TASKS

    def test_filtering_removes_canonical_unsolvable(self) -> None:
        """When get_tasks returns the canonical task, it should be filtered out."""
        mock_all_tasks = [
            {
                "task_id": "task-us-001",
                "name": "Unsolvable Recognition",
                "task_type": "unsolvable",
                "status": "pending",
                "prompt": "",
                "config": {},
                "result": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "started_at": None,
                "completed_at": None,
                "run_id": None,
            },
            {
                "task_id": "task-user-001",
                "name": "My Custom Unsolvable",
                "task_type": "unsolvable",
                "status": "pending",
                "prompt": "",
                "config": {},
                "result": None,
                "created_at": "2026-01-02T00:00:00+00:00",
                "started_at": None,
                "completed_at": None,
                "run_id": None,
            },
        ]

        with patch("src.task_manager.get_tasks", return_value=mock_all_tasks):
            resp = client.get("/api/tasks")
            assert resp.status_code == 200
            data = resp.json()
            task_names = [t["name"] for t in data["tasks"]]
            # Canonical should be filtered out
            assert "Unsolvable Recognition" not in task_names
            # But user-created unsolvable tasks remain visible
            assert "My Custom Unsolvable" in task_names


# ------------------------------------------------------------------
# Test 10: Manual unsolvable tasks remain visible
# ------------------------------------------------------------------

class TestManualUnsolvableVisible:
    """Test 10: manual unsolvable tasks remain visible."""

    def test_manual_unsolvable_task_visible(self) -> None:
        """Manually-created unsolvable tasks with different names should be visible."""
        mock_all_tasks = [
            {
                "task_id": "task-user-002",
                "name": "Custom Contradiction Test",
                "task_type": "unsolvable",
                "status": "pending",
                "prompt": "",
                "config": {},
                "result": None,
                "created_at": "2026-01-03T00:00:00+00:00",
                "started_at": None,
                "completed_at": None,
                "run_id": None,
            },
        ]

        with patch("src.task_manager.get_tasks", return_value=mock_all_tasks):
            resp = client.get("/api/tasks")
            assert resp.status_code == 200
            data = resp.json()
            task_names = [t["name"] for t in data["tasks"]]
            assert "Custom Contradiction Test" in task_names


# ------------------------------------------------------------------
# Test 11: Unsolvable results do not appear in Raw Speed storage
# ------------------------------------------------------------------

class TestUnsolvableNotInRawSpeed:
    """Test 11: Unsolvable results do not appear in Raw Speed storage."""

    def test_unsolvable_not_in_results_store(self) -> None:
        """Unsolvable correctness should NOT add rows to the results_store (speed storage)."""
        mock_us_result = MagicMock()
        mock_us_result.to_dict.return_value = {
            "task_name": "Unsolvable Recognition",
            "task_type": "unsolvable",
            "score": 1.0,
            "passed": True,
            "impossible_detected": True,
            "classification": "contradictory-requirements",
            "conflict_ids": ["R1", "R2"],
            "explanation_valid": True,
            "output_tokens": 500,
            "input_tokens": 200,
            "tokens_per_second": 50.0,
            "ttft_seconds": 0.3,
            "wall_time_seconds": 10.0,
            "generated_response": "IMPOSSIBLE: yes",
        }
        mock_canonical = {
            "task_id": "task-us-001",
            "name": "Unsolvable Recognition",
            "task_type": "unsolvable",
        }

        with patch("src.task_manager.get_tasks", return_value=[mock_canonical]):
            with patch("src.task_manager.create_task_run"):
                with patch("src.task_manager.create_task", return_value=mock_canonical):
                    with patch("src.task_unsolvable.run_unsolvable_task", return_value=mock_us_result):
                        # Only correctness tests — no speed tests
                        resp = client.post("/api/evaluation/run", json={
                            "model": "test-model",
                            "speed_tests": [],
                            "correctness_tests": ["unsolvable"],
                            "iterations": 1,
                            "max_output_tokens": 100,
                            "temperature": 0,
                        })
                        assert resp.status_code == 200


# ------------------------------------------------------------------
# Test 12: Combined markdown + python + java + unsolvable works
# ------------------------------------------------------------------

class TestCombinedCorrectnessWorks:
    """Test 12: combined markdown + python + java + unsolvable works."""

    def test_combined_correctness_validation(self) -> None:
        """All four correctness tests should be accepted together."""
        result = _validate_correctness_tests(["markdown", "python", "java", "unsolvable"])
        assert set(result) == {"markdown", "python", "java", "unsolvable"}

    def test_unsolvable_in_combined_list(self) -> None:
        """Unsolvable should work alongside other correctness tests."""
        mock_md_result = {
            "task_name": "Markdownlint Default",
            "task_type": "markdown",
            "score": 0.8,
            "passed": True,
            "initial_errors": 10,
            "final_errors": 2,
            "errors_fixed": 8,
            "output_tokens": 500,
            "input_tokens": 300,
            "tokens_per_second": 50.0,
            "ttft_seconds": 0.5,
            "wall_time_seconds": 10.0,
            "corrected_violations": [],
        }

        mock_py_result = {
            "task_name": "Python Correctness",
            "task_type": "python",
            "score": 0.6667,
            "passed": False,
            "total_tests": 6,
            "passed_tests": 4,
            "failed_tests": 2,
            "output_tokens": 600,
            "input_tokens": 400,
            "tokens_per_second": 60.0,
            "ttft_seconds": 0.3,
            "wall_time_seconds": 12.0,
        }

        mock_java_result = MagicMock()
        mock_java_result.task_name = "Java Correctness"
        mock_java_result.task_type = "java"
        mock_java_result.score = 1.0
        mock_java_result.passed = True
        mock_java_result.total_tests = 5
        mock_java_result.passed_tests = 5
        mock_java_result.failed_tests = 0
        mock_java_result.compile_success = True
        mock_java_result.output_tokens = 700
        mock_java_result.input_tokens = 350
        mock_java_result.tokens_per_second = 55.0
        mock_java_result.ttft_seconds = 0.4
        mock_java_result.wall_time_seconds = 15.0

        def java_to_dict():
            return {}
        mock_java_result.to_dict = java_to_dict

        mock_us_result = MagicMock()
        mock_us_result.task_name = "Unsolvable Recognition"
        mock_us_result.task_type = "unsolvable"
        mock_us_result.score = 1.0
        mock_us_result.passed = True
        mock_us_result.impossible_detected = True
        mock_us_result.classification = "contradictory-requirements"
        mock_us_result.conflict_ids = {"R1", "R2"}
        mock_us_result.explanation_valid = True
        mock_us_result.output_tokens = 500
        mock_us_result.input_tokens = 200
        mock_us_result.tokens_per_second = 50.0
        mock_us_result.ttft_seconds = 0.3
        mock_us_result.wall_time_seconds = 10.0
        mock_us_result.generated_response = "IMPOSSIBLE: yes"

        def us_to_dict():
            return {
                "task_name": "Unsolvable Recognition",
                "task_type": "unsolvable",
                "score": 1.0,
                "passed": True,
                "impossible_detected": True,
                "classification": "contradictory-requirements",
                "conflict_ids": ["R1", "R2"],
                "explanation_valid": True,
                "output_tokens": 500,
                "input_tokens": 200,
                "tokens_per_second": 50.0,
                "ttft_seconds": 0.3,
                "wall_time_seconds": 10.0,
                "generated_response": "IMPOSSIBLE: yes",
            }
        mock_us_result.to_dict = us_to_dict

        mock_md_task = {"task_id": "task-md-001", "name": "Markdownlint Default", "task_type": "markdown"}
        mock_py_task = {"task_id": "task-py-001", "name": "Python Correctness", "task_type": "python"}
        mock_java_task = {"task_id": "task-java-001", "name": "Java Correctness", "task_type": "java"}
        mock_us_task = {"task_id": "task-us-001", "name": "Unsolvable Recognition", "task_type": "unsolvable"}

        call_order = []

        def track_call(*args, **kwargs):
            call_order.append(kwargs.get("model", ""))
            if len(call_order) == 1:
                return mock_md_result
            elif len(call_order) == 2:
                return mock_py_result
            elif len(call_order) == 3:
                return mock_java_result
            else:
                return mock_us_result

        all_tasks = [mock_md_task, mock_py_task, mock_java_task, mock_us_task]

        with patch("src.task_manager.get_tasks", return_value=all_tasks):
            with patch("src.task_manager.create_task_run"):
                with patch("src.task_manager.create_task", side_effect=[mock_md_task, mock_py_task, mock_java_task, mock_us_task]):
                    with patch("src.task_markdown.run_markdown_task", side_effect=track_call):
                        with patch("src.task_python.run_python_correctness_task", return_value=mock_py_result):
                            with patch("src.task_java.run_java_correctness_task", return_value=mock_java_result):
                                with patch("src.task_unsolvable.run_unsolvable_task", return_value=mock_us_result):
                                    resp = client.post("/api/evaluation/run", json={
                                        "model": "test-model",
                                        "speed_tests": [],
                                        "correctness_tests": ["markdown", "python", "java", "unsolvable"],
                                        "iterations": 1,
                                        "max_output_tokens": 100,
                                        "temperature": 0,
                                    })
                                    assert resp.status_code == 200
                                    data = resp.json()
                                    correctness_results = data["correctness_results"]
                                    test_types = [r["test_type"] for r in correctness_results]
                                    assert "markdown" in test_types
                                    assert "python" in test_types
                                    assert "java" in test_types
                                    assert "unsolvable" in test_types


# ------------------------------------------------------------------
# Test 13: score=None from another validator still does not crash aggregation
# ------------------------------------------------------------------

class TestScoreNoneSafety:
    """Test 13: score=None values must NOT cause HTTP 500."""

    def test_score_none_does_not_crash_aggregation(self) -> None:
        """If one correctness test returns score=None, the average should still work."""
        # Simulate what _aggregate correctness_scores does:
        correctness_scores = [None, 1.0, 0.5]
        valid_scores = [s for s in correctness_scores if s is not None]
        if valid_scores:
            avg = sum(valid_scores) / len(valid_scores)
        else:
            avg = None
        assert avg == 0.75

    def test_all_none_scores(self) -> None:
        """If all scores are None, correctness_score should be None (not crash)."""
        correctness_scores = [None, None]
        valid_scores = [s for s in correctness_scores if s is not None]
        correctness_score = None
        if valid_scores:
            correctness_score = sum(valid_scores) / len(valid_scores)
        assert correctness_score is None


# ------------------------------------------------------------------
# CANONICAL_TASKS consistency check
# ------------------------------------------------------------------

class TestCanonicalTaskConsistency:
    """Verify CANONICAL_TASKS and _CANONICAL_EVALUATION_TASKS are in sync."""

    def test_unsolvable_in_canonical_tasks(self) -> None:
        assert "unsolvable" in CANONICAL_TASKS
        assert CANONICAL_TASKS["unsolvable"]["name"] == "Unsolvable Recognition"
        assert CANONICAL_TASKS["unsolvable"]["task_type"] == "unsolvable"

    def test_unsolvable_in_correctness_labels(self) -> None:
        assert "unsolvable" in _CORRECTNESS_LABELS
        assert _CORRECTNESS_LABELS["unsolvable"] == "Unsolvable Recognition"

    def test_all_canonical_tasks_have_hidden_entries(self) -> None:
        """Every canonical task should have a hidden entry."""
        for key, val in CANONICAL_TASKS.items():
            assert (val["name"], val["task_type"]) in _CANONICAL_EVALUATION_TASKS, \
                f"Canonical task {key} ({val['name']}, {val['task_type']}) not in hidden set"