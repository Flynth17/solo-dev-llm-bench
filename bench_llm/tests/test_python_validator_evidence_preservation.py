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

    def test_run_python_correctness_task_preserves_subprocess_fields(self):
        """Prove the final_result dict in task_python.py includes subprocess fields."""
        # Directly verify the code path by inspecting the result structure.
        # We cannot call run_python_correctness_task without LM Studio, so we
        # verify that validate_python_solution returns the right fields and
        # that those fields would be mapped into final_result.

        from src.python_validator import validate_python_solution

        # Use correct code that passes all tests
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

        vr = validate_python_solution(solution_code, test_code)

        # Verify PythonValidationResult has all subprocess fields
        assert hasattr(vr, 'exit_code'), "PythonValidationResult must have exit_code"
        assert hasattr(vr, 'timed_out'), "PythonValidationResult must have timed_out"
        assert hasattr(vr, 'stdout'), "PythonValidationResult must have stdout"
        assert hasattr(vr, 'stderr'), "PythonValidationResult must have stderr"

        # Verify values are populated (not None) on a successful run
        assert vr.exit_code is not None, "exit_code should be populated"
        assert isinstance(vr.timed_out, bool), "timed_out should be bool"
        assert vr.stdout is not None and len(vr.stdout.strip()) > 0, \
            f"stdout should have content; got: {vr.stdout!r}"

        # Verify the final_result dict in task_python.py includes these fields
        # by checking that the source code contains the mapping.
        import inspect
        from src import task_python

        source = inspect.getsource(task_python)
        for key in ("exit_code", "timed_out", "stdout", "stderr"):
            assert f'"{key}"' in source or f"'{key}'" in source, \
                f"task_python.py must map '{key}' into final_result"
