"""Tests for independence between Python and Unsolvable correctness tasks.

Ensures that Python and Unsolvable results are fully independent — neither
can corrupt or modify the other's result fields during a combined evaluation run.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ------------------------------------------------------------------
# Helpers to build mock task results
# ------------------------------------------------------------------

def _mock_python_result(score: float = 1.0, passed_tests: int = 6, failed_tests: int = 0) -> dict:
    """Build a mock Python correctness result."""
    return {
        "task_name": "Python Correctness",
        "task_type": "python",
        "model": "test-model",
        "score": score,
        "passed": passed_tests == 6,
        "total_tests": 6,
        "passed_tests": passed_tests,
        "failed_tests": failed_tests,
        "output_tokens": 500,
        "input_tokens": 272,
        "tokens_per_second": 150.0,
        "ttft_seconds": 0.5,
        "wall_time_seconds": 3.5,
        "generated_code": "# fixed code",
        "validator_error": "",
        "timestamp": "2026-08-16T12:00:00+00:00",
        "hardware_label": "local",
        "execution_environment": "Local",
        "connection_type": "",
    }


def _mock_unsolvable_result(score: float = 1.0, passed: bool = True) -> MagicMock:
    """Build a mock UnsolvableResultData."""
    result = MagicMock()
    result.task_name = "Unsolvable Recognition"
    result.task_type = "unsolvable"
    result.model = "test-model"
    result.score = score
    result.passed = passed
    result.impossible_detected = True
    result.classification = "logical-contradiction"
    result.conflict_ids = {"R1", "R2"}
    result.explanation_valid = True
    result.output_tokens = 300
    result.input_tokens = 250
    result.tokens_per_second = 100.0
    result.ttft_seconds = 0.3
    result.wall_time_seconds = 2.5
    result.generated_response = "IMPOSSIBLE: yes ..."
    result.timestamp = "2026-08-16T12:00:00+00:00"
    result.hardware_label = "local"
    result.connection_type = "local"

    def to_dict():
        return {
            "task_name": result.task_name,
            "task_type": result.task_type,
            "model": result.model,
            "score": result.score,
            "passed": result.passed,
            "impossible_detected": result.impossible_detected,
            "classification": result.classification,
            "conflict_ids": sorted(result.conflict_ids),
            "explanation_valid": result.explanation_valid,
            "output_tokens": result.output_tokens,
            "input_tokens": result.input_tokens,
            "tokens_per_second": result.tokens_per_second,
            "ttft_seconds": result.ttft_seconds,
            "wall_time_seconds": result.wall_time_seconds,
            "generated_response": result.generated_response,
            "timestamp": result.timestamp,
            "hardware_label": result.hardware_label,
            "connection_type": result.connection_type,
        }

    result.to_dict = to_dict
    return result


# ------------------------------------------------------------------
# Test 1: Python pass + Unsolvable fail → both results independent
# ------------------------------------------------------------------

class TestPythonPassUnsolvableFail:
    """Scenario: Python=6/6 (score=1.0), Unsolvable=fail (score=0.0)."""

    @pytest.mark.asyncio
    async def test_python_pass_unsolvable_fail(self):
        """Combined evaluation must preserve both results independently."""

        py_mock = _mock_python_result(score=1.0, passed_tests=6)
        us_mock = _mock_unsolvable_result(score=0.0, passed=False)

        with patch("src.routes.evaluation._get_results_store") as mock_store, \
             patch("src.routes.evaluation._get_or_create_canonical_task", return_value="task-test-1"), \
             patch("src.task_python.run_python_correctness_task", new_callable=AsyncMock, return_value=py_mock), \
             patch("src.task_unsolvable.run_unsolvable_task", new_callable=AsyncMock, return_value=us_mock), \
             patch("src.routes.evaluation.src.task_manager.create_task_run"), \
             patch("src.routes.evaluation.src.app_state"):

            from fastapi.testclient import TestClient
            from src.main import app

            client = TestClient(app)
            resp = client.post(
                "/api/evaluation/run",
                json={
                    "lm_studio_url": "http://localhost:1234",
                    "model": "test-model",
                    "execution_environment": "Local",
                    "connection_type": "",
                    "hardware_label": "local",
                    "iterations": 1,
                    "max_output_tokens": 500,
                    "temperature": 0,
                    "speed_tests": [],
                    "correctness_tests": ["python", "unsolvable"],
                },
            )

            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            correctness_results = data.get("correctness_results", [])

            # Find Python and Unsolvable results by test_type
            py_result = None
            us_result = None
            for cr in correctness_results:
                if cr["test_type"] == "python":
                    py_result = cr
                elif cr["test_type"] == "unsolvable":
                    us_result = cr

            # Assert Python result is correct (6/6, score=1.0)
            assert py_result is not None, "Python result missing from correctness_results"
            assert py_result["score"] == 1.0, f"Expected Python score=1.0, got {py_result['score']}"
            assert py_result["passed"] is True, f"Expected Python passed=True, got {py_result['passed']}"
            assert py_result["passed_tests"] == 6, f"Expected Python passed_tests=6, got {py_result['passed_tests']}"
            assert py_result["failed_tests"] == 0, f"Expected Python failed_tests=0, got {py_result['failed_tests']}"

            # Assert Unsolvable result is correct (score=0.0)
            assert us_result is not None, "Unsolvable result missing from correctness_results"
            assert us_result["score"] == 0.0, f"Expected Unsolvable score=0.0, got {us_result['score']}"
            assert us_result["passed"] is False, f"Expected Unsolvable passed=False, got {us_result['passed']}"


# ------------------------------------------------------------------
# Test 2: Python fail + Unsolvable pass → both results independent
# ------------------------------------------------------------------

class TestPythonFailUnsolvablePass:
    """Scenario: Python=fail (score=0.0), Unsolvable=pass (score=1.0)."""

    @pytest.mark.asyncio
    async def test_python_fail_unsolvable_pass(self):
        """Combined evaluation must preserve both results independently."""
        py_mock = _mock_python_result(score=0.0, passed_tests=0)
        us_mock = _mock_unsolvable_result(score=1.0, passed=True)

        with patch("src.routes.evaluation._get_results_store") as mock_store, \
             patch("src.routes.evaluation._get_or_create_canonical_task", return_value="task-test-2"), \
             patch("src.task_python.run_python_correctness_task", new_callable=AsyncMock, return_value=py_mock), \
             patch("src.task_unsolvable.run_unsolvable_task", new_callable=AsyncMock, return_value=us_mock), \
             patch("src.routes.evaluation.src.task_manager.create_task_run"), \
             patch("src.routes.evaluation.src.app_state"):

            from fastapi.testclient import TestClient
            from src.main import app

            client = TestClient(app)
            resp = client.post(
                "/api/evaluation/run",
                json={
                    "lm_studio_url": "http://localhost:1234",
                    "model": "test-model",
                    "execution_environment": "Local",
                    "connection_type": "",
                    "hardware_label": "local",
                    "iterations": 1,
                    "max_output_tokens": 500,
                    "temperature": 0,
                    "speed_tests": [],
                    "correctness_tests": ["python", "unsolvable"],
                },
            )

            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            correctness_results = data.get("correctness_results", [])

            # Find Python and Unsolvable results by test_type
            py_result = None
            us_result = None
            for cr in correctness_results:
                if cr["test_type"] == "python":
                    py_result = cr
                elif cr["test_type"] == "unsolvable":
                    us_result = cr

            # Assert Python result is correct (score=0.0)
            assert py_result is not None, "Python result missing from correctness_results"
            assert py_result["score"] == 0.0, f"Expected Python score=0.0, got {py_result['score']}"
            assert py_result["passed"] is False, f"Expected Python passed=False, got {py_result['passed']}"

            # Assert Unsolvable result is correct (100%)
            assert us_result is not None, "Unsolvable result missing from correctness_results"
            assert us_result["score"] == 1.0, f"Expected Unsolvable score=1.0, got {us_result['score']}"
            assert us_result["passed"] is True, f"Expected Unsolvable passed=True, got {us_result['passed']}"


# ------------------------------------------------------------------
# Test 3: Validator independence — no shared mutable state
# ------------------------------------------------------------------

class TestValidatorIndependence:
    """Ensure the two validators don't share any mutable global state."""

    def test_python_validator_no_unsolvable_state(self):
        """Python validator must not import or reference unsolvable module."""
        from src.python_validator import validate_python_solution, PythonValidationResult
        import inspect

        source = inspect.getsource(validate_python_solution)
        assert "unsolvable" not in source.lower(), \
            "Python validator references unsolvable module — potential shared state"

    def test_unsolvable_validator_no_python_state(self):
        """Unsolvable validator must not import or reference Python validator."""
        from src.unsolvable_validator import validate_unsolvable_response, UnsolvableResult
        import inspect

        source = inspect.getsource(validate_unsolvable_response)
        assert "python_validator" not in source.lower(), \
            "Unsolvable validator references python_validator — potential shared state"

    def test_different_result_types(self):
        """Python and Unsolvable must return different result types."""
        from src.python_validator import PythonValidationResult
        from dataclasses import is_dataclass as _is_dataclass

        py_result = PythonValidationResult()
        assert _is_dataclass(py_result), "Python result should be a dataclass"

        # Unsolvable returns an UnsolvableResult (also a dataclass)
        from src.unsolvable_validator import UnsolvableResult
        us_result = UnsolvableResult(score=0.0, passed=False, impossible_detected=False,
                                     classification="", conflict_ids=set(), explanation_valid=False)
        assert _is_dataclass(us_result), "Unsolvable result should be a dataclass"

        # They must be different types
        assert type(py_result) is not type(us_result), \
            "Python and Unsolvable results must have different types"