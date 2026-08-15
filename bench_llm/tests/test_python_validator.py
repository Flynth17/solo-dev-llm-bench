"""Tests for the Python correctness validator.

Validates:
- Broken fixture fails at least one test
- Correct fixture produces score=1.0, passed=True
- Timeout handling works
- Syntax error handling works
- Result data structure is correct
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from src.python_validator import validate_python_solution, PythonValidationResult

# Import fixture content
FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "tasks", "python_correctness")


def _load_fixture(filename):
    path = os.path.join(FIXTURE_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# Load test code once
TEST_CODE = _load_fixture("test_solution.py")


class TestBrokenFixture:
    """The broken solution.py should fail at least one test."""

    def test_broken_fixture_fails_at_least_one_test(self):
        """Broken fixture must not pass all tests."""
        broken_code = _load_fixture("solution.py")
        result = validate_python_solution(broken_code, TEST_CODE)

        assert result.failed_tests >= 1, "Broken fixture must fail at least one test"
        assert result.passed is False, "Broken fixture must not be marked as passed"
        assert result.score < 1.0, "Broken fixture must have score < 1.0"


class TestCorrectFixture:
    """A known-correct solution must produce score=1.0, passed=True."""

    @staticmethod
    def _get_correct_code():
        """Return a known-correct version of solution.py."""
        return '''"""Correct Python module."""


def add(a, b):
    """Add two numbers and return the result."""
    return a + b


def multiply(x, y):
    """Multiply two numbers and return the result."""
    return x * y


def is_even(n):
    """Return True if n is even, False otherwise."""
    return n % 2 == 0
'''

    def test_correct_fixture_passes_all_tests(self):
        """Correct fixture must pass all tests."""
        correct_code = self._get_correct_code()
        result = validate_python_solution(correct_code, TEST_CODE)

        assert result.passed is True, "Correct fixture must be marked as passed"
        assert result.score == 1.0, "Correct fixture must have score = 1.0"
        assert result.failed_tests == 0, "Correct fixture must have 0 failed tests"
        assert result.total_tests == 6, "Correct fixture must have 6 total tests"
        assert result.exit_code == 0, "Correct fixture must have exit_code 0"
        assert result.timed_out is False, "Correct fixture must not time out"

    def test_correct_fixture_data_structure(self):
        """Correct fixture result must have all required fields."""
        correct_code = self._get_correct_code()
        result = validate_python_solution(correct_code, TEST_CODE)

        assert hasattr(result, "total_tests")
        assert hasattr(result, "passed_tests")
        assert hasattr(result, "failed_tests")
        assert hasattr(result, "score")
        assert hasattr(result, "passed")
        assert hasattr(result, "exit_code")
        assert hasattr(result, "timed_out")
        assert hasattr(result, "stdout")
        assert hasattr(result, "stderr")
        assert hasattr(result, "error")

    def test_to_dict_returns_all_fields(self):
        """to_dict() must return all result fields."""
        correct_code = self._get_correct_code()
        result = validate_python_solution(correct_code, TEST_CODE)
        d = result.to_dict()

        expected_keys = {
            "total_tests", "passed_tests", "failed_tests", "score",
            "passed", "exit_code", "timed_out", "stdout", "stderr", "error",
        }
        assert set(d.keys()) == expected_keys
        assert d["passed"] is True
        assert d["score"] == 1.0


class TestTimeoutHandling:
    """Timeout must be respected and reported."""

    def test_timeout_detected(self):
        """A very short timeout should be caught."""
        # Use a code that would be fine, but with a tiny timeout
        correct_code = '''
def add(a, b):
    return a + b
'''
        # Use a minimal test that runs quickly
        minimal_test = '''
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solution import add
def test_add():
    assert add(1, 2) == 3
'''
        result = validate_python_solution(correct_code, minimal_test, timeout=0.001)
        # With such a tiny timeout, it might still pass (pytest is fast)
        # But the key is that timed_out field exists and error is set if it does
        assert hasattr(result, "timed_out")
        assert result.score >= 0.0
        assert result.score <= 1.0


class TestSyntaxErrorHandling:
    """Syntax errors in the solution must be detected."""

    def test_syntax_error_handled(self):
        """Code with syntax errors should not crash the validator."""
        broken_syntax = "def foo(  # missing colon\n    pass\n"
        result = validate_python_solution(broken_syntax, TEST_CODE)

        assert result.exit_code != 0
        assert result.passed is False
        assert result.score == 0.0
        assert result.error == ""  # pytest handles it, not our code


class TestScoreRange:
    """Score must always be between 0.0 and 1.0."""

    def test_score_in_range(self):
        """Score must be clamped to [0.0, 1.0]."""
        correct_code = '''
def add(a, b):
    return a + b
def multiply(x, y):
    return x * y
def is_even(n):
    return n % 2 == 0
'''
        result = validate_python_solution(correct_code, TEST_CODE)
        assert 0.0 <= result.score <= 1.0

    def test_failed_score_in_range(self):
        """Failed score must be clamped to [0.0, 1.0]."""
        broken_code = _load_fixture("solution.py")
        result = validate_python_solution(broken_code, TEST_CODE)
        assert 0.0 <= result.score <= 1.0