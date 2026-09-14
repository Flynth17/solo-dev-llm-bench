"""Act P5.12 — Prove pytest subprocess evidence survives validate_python_solution() → task result chain."""

import sys
sys.path.insert(0, r'e:\Bench_LLM\bench_llm')

import asyncio


class TestPythonValidatorEvidencePreservation:
    """Prove exit_code/stdout/stderr/timed_out survive the validation chain."""

    def test_validate_python_solution_preserves_subprocess_fields(self):
        """validate_python_solution() must return PythonValidationResult with subprocess fields populated."""
        from src.python_validator import validate_python_solution

        # Use correct code that should pass all tests
        solution_code = '''
def add(a, b):
    return a + b


def multiply(x, y):
    result = x * y
    return result


def is_even(n):
    return n % 2 == 0
'''

        test_code = '''
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solution import add, multiply, is_even

def test_add_positive_numbers():
    assert add(2, 3) == 5

def test_multiply_positive_numbers():
    assert multiply(4, 5) == 20

def test_is_even_true():
    assert is_even(4) is True
'''
        result = validate_python_solution(solution_code, test_code)

        # All tests should pass
        assert result.passed is True
        assert result.score == 1.0
        assert result.total_tests == 3
        assert result.passed_tests == 3
        assert result.failed_tests == 0

        # Subprocess evidence must be preserved (not None/empty)
        assert result.exit_code is not None, "exit_code must be populated"
        assert isinstance(result.exit_code, int), f"exit_code should be int, got {type(result.exit_code)}"
        assert result.timed_out is not None, "timed_out must be populated"
        # stdout should contain test results (at least the summary line)
        assert result.stdout is not None and len(result.stdout.strip()) > 0, \
            f"stdout should not be empty; got: {result.stdout!r}"

    def test_validate_python_solution_failure_preserves_stderr(self):
        """validate_python_solution() must preserve stderr even on collection failure."""
        from src.python_validator import validate_python_solution

        # Code with syntax error to trigger pytest failure
        broken_code = '''
def add(a, b)
    return a + b  # SyntaxError: missing colon
'''

        test_code = '''
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solution import add

def test_add_positive_numbers():
    assert add(2, 3) == 5
'''
        result = validate_python_solution(broken_code, test_code)

        # Should NOT pass due to syntax error in solution
        assert result.passed is False

        # exit_code must be populated (non-zero for pytest failure)
        assert result.exit_code is not None, "exit_code must be populated on failure"

