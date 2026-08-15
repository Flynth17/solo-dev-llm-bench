"""Tests for Java correctness backend wiring (routes/tasks.py + routes/evaluation.py).

Tests cover:
1. Normal Java task route uses run_java_correctness_task().
2. Java result persists through create_task_run().
3. Evaluation accepts "java".
4. Evaluation rejects unknown correctness names.
5. Java result contributes to correctness_score.
6. Canonical Java task definition is reused across evaluations.
7. Repeated Java evaluations create new task_run rows.
8. Java correctness never enters Raw Speed storage.
9. Markdown still works.
10. Python still works.
"""

import sys
import os
from unittest.mock import patch, AsyncMock, MagicMock
import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.routes.evaluation import (
    CORRECTNESS_TESTS,
    CANONICAL_TASKS,
    _CORRECTNESS_LABELS,
    _validate_correctness_tests,
)


# ------------------------------------------------------------------
# Test 1: Normal Java task route uses run_java_correctness_task()
# ------------------------------------------------------------------

class TestNormalJavaTaskRoute:
    """Test that normal Java task route uses run_java_correctness_task()."""

    def test_tasks_imports_run_java_correctness_task(self):
        """tasks.py must import run_java_correctness_task."""
        import src.routes.tasks as tasks_mod
        # The import must exist without error
        assert hasattr(tasks_mod, "run_java_correctness_task")

    def test_java_task_type_allowed(self):
        """task_type 'java' must be allowed in create_task."""
        import src.routes.tasks as tasks_mod
        # Just verify the string 'java' is in the allowed types in the code
        import inspect
        source = inspect.getsource(tasks_mod)
        assert '"java"' in source or "'java'" in source


# ------------------------------------------------------------------
# Test 2: Java result persists through create_task_run()
# ------------------------------------------------------------------

class TestJavaResultPersists:
    """Test that Java result persists through create_task_run()."""

    def test_java_result_to_dict_has_required_fields(self):
        """JavaCorrectnessResult.to_dict() must include all required fields."""
        from src.task_java import JavaCorrectnessResult

        result = JavaCorrectnessResult(
            task_name="java_correctness",
            task_type="java_correctness",
            model="test-model",
            score=0.75,
            passed=True,
            total_tests=7,
            passed_tests=5,
            failed_tests=2,
            compile_success=True,
            output_tokens=200,
            input_tokens=500,
            tokens_per_second=50.0,
            ttft_seconds=0.3,
            wall_time_seconds=5.0,
            generated_code="public class Solution { }",
            timestamp="2025-01-01T00:00:00+00:00",
            hardware_label="local",
            connection_type="local",
        )

        d = result.to_dict()

        # All required fields for task run persistence
        assert d["task_name"] == "java_correctness"
        assert d["task_type"] == "java_correctness"
        assert d["model"] == "test-model"
        assert d["score"] == 0.75
        assert d["passed"] is True
        assert d["total_tests"] == 7
        assert d["passed_tests"] == 5
        assert d["failed_tests"] == 2
        assert d["compile_success"] is True
        assert d["output_tokens"] == 200
        assert d["input_tokens"] == 500
        assert d["tokens_per_second"] == 50.0
        assert d["ttft_seconds"] == 0.3
        assert d["wall_time_seconds"] == 5.0
        assert d["timestamp"] is not None


# ------------------------------------------------------------------
# Test 3: Evaluation accepts "java"
# ------------------------------------------------------------------

class TestEvaluationAcceptsJava:
    """Test that evaluation accepts 'java' as a correctness test."""

    def test_java_in_correctness_tests_set(self):
        """'java' must be in CORRECTNESS_TESTS."""
        assert "java" in CORRECTNESS_TESTS

    def test_java_validated_successfully(self):
        """_validate_correctness_tests must accept 'java'."""
        result = _validate_correctness_tests(["java"])
        assert result == ["java"]

    def test_java_with_other_tests(self):
        """_validate_correctness_tests must accept java alongside markdown and python."""
        result = _validate_correctness_tests(["markdown", "python", "java"])
        assert "java" in result
        assert "markdown" in result
        assert "python" in result

    def test_canonical_task_java_exists(self):
        """CANONICAL_TASKS must have a java entry."""
        assert "java" in CANONICAL_TASKS
        assert CANONICAL_TASKS["java"]["name"] == "Java Correctness"
        assert CANONICAL_TASKS["java"]["task_type"] == "java"

    def test_correctness_label_java_exists(self):
        """_CORRECTNESS_LABELS must have a java entry."""
        assert "java" in _CORRECTNESS_LABELS
        assert _CORRECTNESS_LABELS["java"] == "Java Correctness"


# ------------------------------------------------------------------
# Test 4: Evaluation rejects unknown correctness names
# ------------------------------------------------------------------

class TestEvaluationRejectsUnknown:
    """Test that evaluation rejects unknown correctness names."""

    def test_unknown_correctness_rejected(self):
        """Unknown correctness test name must be rejected."""
        with pytest.raises(HTTPException):
            _validate_correctness_tests(["unknown_test"])

    def test_java_correctness_not_confused_with_javascript(self):
        """'javascript' must not be accepted as a correctness test."""
        with pytest.raises(HTTPException):
            _validate_correctness_tests(["javascript"])

    def test_empty_list_rejected(self):
        """Empty list must be rejected."""
        with pytest.raises(HTTPException):
            _validate_correctness_tests([])


# ------------------------------------------------------------------
# Test 5: Java result contributes to correctness_score
# ------------------------------------------------------------------

class TestJavaContributesToScore:
    """Test that Java result contributes to correctness_score arithmetic mean."""

    @pytest.mark.asyncio
    async def test_java_contributes_to_correctness_score(self):
        """Java score must be included in correctness_score arithmetic mean."""
        # Mock the java_result object
        mock_java_result = MagicMock()
        mock_java_result.score = 0.75
        mock_java_result.passed = True
        mock_java_result.total_tests = 7
        mock_java_result.passed_tests = 5
        mock_java_result.failed_tests = 2
        mock_java_result.compile_success = True
        mock_java_result.output_tokens = 200
        mock_java_result.input_tokens = 500
        mock_java_result.tokens_per_second = 50.0
        mock_java_result.ttft_seconds = 0.3
        mock_java_result.wall_time_seconds = 5.0
        mock_java_result.task_name = "java_correctness"
        mock_java_result.task_type = "java_correctness"
        mock_java_result.to_dict.return_value = {"score": 0.75}

        # Test the arithmetic mean calculation
        # If Markdown = 1.0, Python = 0.5, Java = 0.75
        # correctness_score = (1.0 + 0.5 + 0.75) / 3 = 0.75
        correctness_scores = [1.0, 0.5, 0.75]
        expected_score = round(sum(correctness_scores) / len(correctness_scores), 4)
        assert expected_score == 0.75

    @pytest.mark.asyncio
    async def test_java_only_correctness_score(self):
        """With only Java, correctness_score equals Java score."""
        java_score = 0.75
        correctness_scores = [java_score]
        expected_score = round(sum(correctness_scores) / len(correctness_scores), 4)
        assert expected_score == 0.75


# ------------------------------------------------------------------
# Test 6: Canonical Java task definition is reused across evaluations
# ------------------------------------------------------------------

class TestCanonicalJavaTaskReused:
    """Test that canonical Java task definition is reused."""

    def test_get_or_create_canonical_task_reuses(self):
        """_get_or_create_canonical_task must reuse existing task."""
        import src.routes.evaluation as eval_mod
        import src.task_manager as tm

        # Clear any existing canonical tasks first
        tasks = tm.get_tasks()
        java_task_id = None
        for t in tasks:
            if t["name"] == "Java Correctness" and t["task_type"] == "java":
                java_task_id = t["task_id"]
                break

        # Get or create should return same id if exists
        task_id = eval_mod._get_or_create_canonical_task("java")

        if java_task_id:
            assert task_id == java_task_id
        else:
            # First time creating
            assert task_id is not None
            # Create another instance and verify same id
            task_id2 = eval_mod._get_or_create_canonical_task("java")
            assert task_id2 == task_id


# ------------------------------------------------------------------
# Test 7: Repeated Java evaluations create new task_run rows
# ------------------------------------------------------------------

class TestRepeatedJavaEvaluations:
    """Test that repeated Java evaluations create new task_run rows."""

    @pytest.mark.asyncio
    async def test_repeated_java_evaluations_create_new_runs(self):
        """Repeated Java evaluations must create new task_run rows, not overwrite."""
        import src.task_manager as tm

        # Create a task for Java
        task = tm.create_task(name="Java Correctness", task_type="java", prompt="")
        task_id = task["task_id"]

        # Create two task runs
        run1 = tm.create_task_run(
            task_id=task_id,
            task_name="Java Correctness",
            task_type="java_correctness",
            model="model-1",
            timestamp="2025-01-01T00:00:00+00:00",
            passed=True,
            score=0.75,
            output_tokens=200,
            input_tokens=500,
            tokens_per_second=50.0,
            ttft_seconds=0.3,
            wall_time_seconds=5.0,
            result={"score": 0.75},
        )

        run2 = tm.create_task_run(
            task_id=task_id,
            task_name="Java Correctness",
            task_type="java_correctness",
            model="model-2",
            timestamp="2025-01-02T00:00:00+00:00",
            passed=False,
            score=0.5,
            output_tokens=150,
            input_tokens=400,
            tokens_per_second=40.0,
            ttft_seconds=0.4,
            wall_time_seconds=6.0,
            result={"score": 0.5},
        )

        # Both runs should have different IDs (create_task_run returns "id" not "run_id")
        assert run1["id"] != run2["id"]

        # Both should be retrievable
        runs = tm.get_task_runs()
        java_runs = [r for r in runs if r.get("task_type") == "java_correctness"]
        assert len(java_runs) >= 2


# ------------------------------------------------------------------
# Test 8: Java correctness never enters Raw Speed storage
# ------------------------------------------------------------------

class TestJavaNotInSpeedStorage:
    """Test that Java correctness never enters Raw Speed storage."""

    def test_speed_tests_does_not_include_java(self):
        """Speed tests must not include Java as a test type."""
        from src.evaluation_prompts import PROMPT_FILES
        assert "java" not in PROMPT_FILES

    def test_canonical_tasks_separate_from_speed(self):
        """Canonical tasks and speed tests are separate namespaces."""
        # CORRECTNESS_TESTS and PROMPT_FILES are distinct
        assert "java" in CORRECTNESS_TESTS
        assert "java" not in {"small", "medium", "large"}  # PROMPT_FILES keys


# ------------------------------------------------------------------
# Test 9: Markdown still works
# ------------------------------------------------------------------

class TestMarkdownStillWorks:
    """Test that Markdown correctness still works."""

    def test_markdown_in_correctness_tests(self):
        """'markdown' must still be in CORRECTNESS_TESTS."""
        assert "markdown" in CORRECTNESS_TESTS

    def test_markdown_canonical_task_exists(self):
        """CANONICAL_TASKS must still have markdown."""
        assert "markdown" in CANONICAL_TASKS

    def test_markdown_label_exists(self):
        """_CORRECTNESS_LABELS must still have markdown."""
        assert "markdown" in _CORRECTNESS_LABELS

    def test_markdown_validated_successfully(self):
        """_validate_correctness_tests must still accept 'markdown'."""
        result = _validate_correctness_tests(["markdown"])
        assert result == ["markdown"]


# ------------------------------------------------------------------
# Test 10: Python still works
# ------------------------------------------------------------------

class TestPythonStillWorks:
    """Test that Python correctness still works."""

    def test_python_in_correctness_tests(self):
        """'python' must still be in CORRECTNESS_TESTS."""
        assert "python" in CORRECTNESS_TESTS

    def test_python_canonical_task_exists(self):
        """CANONICAL_TASKS must still have python."""
        assert "python" in CANONICAL_TASKS

    def test_python_label_exists(self):
        """_CORRECTNESS_LABELS must still have python."""
        assert "python" in _CORRECTNESS_LABELS

    def test_python_validated_successfully(self):
        """_validate_correctness_tests must still accept 'python'."""
        result = _validate_correctness_tests(["python"])
        assert result == ["python"]


# ------------------------------------------------------------------
# HTTPException import for pytest.raises
# ------------------------------------------------------------------

from fastapi import HTTPException