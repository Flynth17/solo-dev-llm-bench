"""Tests for the Python correctness task runner.

Tests cover:
1. Fixture loading
2. Prompt construction
3. String LM Studio output
4. List LM Studio output
5. Dict LM Studio output
6. Markdown fence stripping
7. Correct solution -> score 1.0, passed True
8. Partial solution -> partial score
9. Invalid/syntax-broken solution -> failed result
10. Validator result is passed through unchanged
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.task_python import (
    load_fixture_py,
    build_benchmark_prompt,
    normalize_llm_output,
    strip_code_fences,
    TASK_DEFINITION,
)

# Import validator for direct testing
from src.python_validator import validate_python_solution

# Import fixture content
FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "tasks", "python_correctness")


def _load_fixture(filename):
    path = os.path.join(FIXTURE_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------
# Test 1: Fixture loading
# ------------------------------------------------------------------

class TestFixtureLoading:
    """Test that the fixture loads correctly."""

    def test_fixture_loads_content(self):
        """Fixture file must load without error."""
        content = load_fixture_py()
        assert content is not None
        assert len(content) > 0
        assert "def add" in content
        assert "def multiply" in content
        assert "def is_even" in content

    def test_fixture_has_bugs(self):
        """Fixture must contain deliberate bugs."""
        content = load_fixture_py()
        assert "a - b" in content  # BUG in add
        assert "# BUG: missing return" in content  # BUG in multiply

    def test_fixture_not_found_raises(self):
        """Non-existent fixture must raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Fixture not found"):
            load_fixture_py("nonexistent/file.py")


# ------------------------------------------------------------------
# Test 2: Prompt construction
# ------------------------------------------------------------------

class TestPromptConstruction:
    """Test that the prompt is built correctly."""

    def test_prompt_contains_code(self):
        """Prompt must contain the original code."""
        original_code = load_fixture_py()
        prompt = build_benchmark_prompt(original_code)
        assert "def add" in prompt
        assert "def multiply" in prompt
        assert "def is_even" in prompt

    def test_prompt_contains_instructions(self):
        """Prompt must contain fix instructions."""
        prompt = build_benchmark_prompt("some code")
        assert "Fix the bugs" in prompt
        assert "Preserve" in prompt
        assert "Do not explain" in prompt
        assert "Do not wrap" in prompt

    def test_prompt_format_replaces_code(self):
        """Prompt must replace {code} with the actual code."""
        test_code = "def foo(): pass"
        prompt = build_benchmark_prompt(test_code)
        assert test_code in prompt
        assert "{code}" not in prompt


# ------------------------------------------------------------------
# Test 3-5: LM Studio output normalization
# ------------------------------------------------------------------

class TestLlmOutputNormalization:
    """Test normalize_llm_output handles all formats."""

    def test_string_output(self):
        """String output should pass through."""
        text = "def add(a, b):\n    return a + b"
        result = normalize_llm_output(text)
        assert result == text

    def test_dict_output(self):
        """Dict output should extract content."""
        raw = {"content": "def add(a, b):\n    return a + b"}
        result = normalize_llm_output(raw)
        assert "def add" in result

    def test_dict_output_with_str_conversion(self):
        """Dict output without content key should str() the whole thing."""
        raw = {"key": "value"}
        result = normalize_llm_output(raw)
        assert "value" in result

    def test_list_of_dicts_output(self):
        """List of dicts should concatenate content."""
        raw = [
            {"content": "def add(a, b):\n"},
            {"content": "    return a + b"},
        ]
        result = normalize_llm_output(raw)
        assert "def add" in result
        assert "return a + b" in result

    def test_list_of_strings_output(self):
        """List of strings should concatenate."""
        raw = ["def add(a, b):\n", "    return a + b"]
        result = normalize_llm_output(raw)
        assert "def add" in result
        assert "return a + b" in result

    def test_none_output(self):
        """None should be converted to string."""
        result = normalize_llm_output(None)
        assert result == "None"

    def test_empty_list_output(self):
        """Empty list should return empty string."""
        result = normalize_llm_output([])
        assert result == ""


# ------------------------------------------------------------------
# Test 6: Markdown fence stripping
# ------------------------------------------------------------------

class TestFenceStripping:
    """Test strip_code_fences handles all fence types."""

    def test_python_fence(self):
        """```python ... ``` should be stripped."""
        text = "```python\ndef add(a, b):\n    return a + b\n```"
        result = strip_code_fences(text)
        assert not result.startswith("```")
        assert "def add" in result

    def test_py_fence(self):
        """```py ... ``` should be stripped."""
        text = "```py\ndef add(a, b):\n    return a + b\n```"
        result = strip_code_fences(text)
        assert not result.startswith("```")
        assert "def add" in result

    def test_generic_fence(self):
        """``` ... ``` should be stripped."""
        text = "```\ndef add(a, b):\n    return a + b\n```"
        result = strip_code_fences(text)
        assert not result.startswith("```")
        assert "def add" in result

    def test_no_fence(self):
        """Code without fences should pass through."""
        text = "def add(a, b):\n    return a + b"
        result = strip_code_fences(text)
        assert result == text

    def test_fence_with_whitespace(self):
        """Fences with leading/trailing whitespace should be handled."""
        text = "\n  ```python\ndef add(a, b):\n    return a + b\n```\n  "
        result = strip_code_fences(text)
        assert not result.startswith("```")
        assert "def add" in result


# ------------------------------------------------------------------
# Test 7: Correct solution -> score 1.0, passed True
# ------------------------------------------------------------------

class TestCorrectSolution:
    """Test that a correct solution produces perfect score."""

    @staticmethod
    def _get_correct_code():
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

    def test_correct_solution_score(self):
        """Correct solution must produce score 1.0."""
        test_code = _load_fixture("test_solution.py")
        validation = validate_python_solution(self._get_correct_code(), test_code)

        assert validation.score == 1.0
        assert validation.passed is True
        assert validation.failed_tests == 0
        assert validation.total_tests == 6
        assert validation.passed_tests == 6

    def test_correct_result_passthrough(self):
        """Validator result fields must be present in task result."""
        test_code = _load_fixture("test_solution.py")
        validation = validate_python_solution(self._get_correct_code(), test_code)

        # Verify all required fields exist
        assert hasattr(validation, "total_tests")
        assert hasattr(validation, "passed_tests")
        assert hasattr(validation, "failed_tests")
        assert hasattr(validation, "score")
        assert hasattr(validation, "passed")
        assert hasattr(validation, "exit_code")
        assert hasattr(validation, "timed_out")


# ------------------------------------------------------------------
# Test 8: Partial solution -> partial score
# ------------------------------------------------------------------

class TestPartialSolution:
    """Test that a partial solution produces partial score."""

    def test_partial_solution_score(self):
        """Partial solution (only add fixed) must produce score < 1.0."""
        partial_code = '''"""Partially fixed Python module."""


def add(a, b):
    """Add two numbers and return the result."""
    return a + b  # FIXED


def multiply(x, y):
    """Multiply two numbers and return the result."""
    result = x * y
    # BUG: missing return statement


def is_even(n):
    """Return True if n is even, False otherwise."""
    return n % 2 == 1  # BUG: should be == 0
'''
        test_code = _load_fixture("test_solution.py")
        validation = validate_python_solution(partial_code, test_code)

        assert 0.0 < validation.score < 1.0
        assert validation.passed is False
        assert validation.failed_tests > 0

    def test_partial_score_equals_ratio(self):
        """Score must equal passed_tests / total_tests."""
        partial_code = '''
def add(a, b):
    return a + b

def multiply(x, y):
    return x * y

def is_even(n):
    return n % 2 == 1
'''
        test_code = _load_fixture("test_solution.py")
        validation = validate_python_solution(partial_code, test_code)

        if validation.total_tests > 0:
            expected_score = validation.passed_tests / validation.total_tests
            # Validator rounds to 4 decimal places
            assert abs(validation.score - round(expected_score, 4)) < 1e-9


# ------------------------------------------------------------------
# Test 9: Invalid/syntax-broken solution -> failed result
# ------------------------------------------------------------------

class TestInvalidSolution:
    """Test that syntax-broken code produces failed result."""

    def test_syntax_broken_solution(self):
        """Code with syntax errors must produce score 0.0."""
        broken_code = "def foo(  # missing colon\n    pass\n"
        test_code = _load_fixture("test_solution.py")
        validation = validate_python_solution(broken_code, test_code)

        assert validation.passed is False
        assert validation.score == 0.0
        assert validation.exit_code != 0

    def test_empty_solution(self):
        """Empty code must produce score 0.0."""
        validation = validate_python_solution("", _load_fixture("test_solution.py"))

        assert validation.passed is False
        assert validation.score == 0.0


# ------------------------------------------------------------------
# Test 10: Validator result is passed through unchanged
# ------------------------------------------------------------------

class TestValidatorPassthrough:
    """Test that validator results are passed through correctly."""

    def test_validator_to_dict(self):
        """validator result to_dict() must return all fields."""
        correct_code = '''
def add(a, b):
    return a + b
def multiply(x, y):
    return x * y
def is_even(n):
    return n % 2 == 0
'''
        test_code = _load_fixture("test_solution.py")
        validation = validate_python_solution(correct_code, test_code)
        d = validation.to_dict()

        expected_keys = {
            "total_tests", "passed_tests", "failed_tests", "score",
            "passed", "exit_code", "timed_out", "stdout", "stderr", "error",
        }
        assert set(d.keys()) == expected_keys


# ------------------------------------------------------------------
# Test TASK_DEFINITION constants
# ------------------------------------------------------------------

class TestTaskDefinition:
    """Test that TASK_DEFINITION has required keys."""

    def test_task_name(self):
        assert TASK_DEFINITION["name"] == "Python Correctness"

    def test_task_type(self):
        assert TASK_DEFINITION["task_type"] == "python"

    def test_validator(self):
        assert TASK_DEFINITION["validator"] == "pytest"

    def test_max_output_tokens(self):
        assert TASK_DEFINITION["max_output_tokens"] == 1024

    def test_temperature(self):
        assert TASK_DEFINITION["temperature"] == 0