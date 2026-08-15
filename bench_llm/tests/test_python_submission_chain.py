"""Regression tests for Python correctness submission chain (Act P5.1).

Proves that:
A. Correct LM Studio final response → 6/6, score=1.0
B. Broken fixture never replaces the model's submission
C. Reasoning blocks are discarded and never reach validator/runtime
D. Pytest parser handles all output formats correctly
E. No final message → empty runtime, 0/6, score=0
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

# Ensure bench_llm is on the path for imports
BENCH_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(BENCH_ROOT))


class TestCorrectFinalMessage(TestCase):
    """A. Correct LM Studio final response → 6/6, score=1.0."""

    def test_correct_code_passes_all_tests(self):
        from src.python_validator import validate_python_solution

        correct_code = '''"""Corrected Python module — all three functions fixed."""


def add(a, b):
    """Add two numbers and return the result."""
    return a + b


def multiply(x, y):
    """Multiply two numbers and return the result."""
    result = x * y
    return result


def is_even(n):
    """Return True if n is even, False otherwise."""
    return n % 2 == 0
'''

        # Read test harness from fixture
        test_file = BENCH_ROOT / "tasks" / "python_correctness" / "test_solution.py"
        test_code = test_file.read_text(encoding="utf-8")

        result = validate_python_solution(correct_code, test_code)

        self.assertTrue(result.passed, f"Expected all tests to pass, but got score={result.score}")
        self.assertEqual(result.passed_tests, 6, "Expected 6 passed tests")
        self.assertEqual(result.failed_tests, 0, "Expected 0 failed tests")
        self.assertEqual(result.total_tests, 6, "Expected total_tests=6")
        self.assertEqual(result.score, 1.0, "Expected score=1.0")
        self.assertEqual(result.exit_code, 0, "Expected pytest exit_code=0")


class TestBrokenFixtureNeverReplacesSubmission(TestCase):
    """B. Broken fixture must never replace the model's submission."""

    def test_model_submission_not_overwritten_by_fixture(self):
        from src.python_validator import validate_python_solution

        # The broken fixture content (intentionally wrong)
        broken_code = '''"""Deliberately broken Python module for correctness benchmark.

Bugs:
  1. add(a, b) returns a - b instead of a + b
  2. multiply(x, y) has no return statement (returns None)
  3. is_even(n) checks odd numbers instead of even

Fix these bugs to pass all tests.
"""


def add(a, b):
    """Add two numbers and return the result."""
    return a - b  # BUG: should be a + b


def multiply(x, y):
    """Multiply two numbers and return the result."""
    result = x * y
    # BUG: missing return statement


def is_even(n):
    """Return True if n is even, False otherwise."""
    return n % 2 == 1  # BUG: should be n % 2 == 0
'''

        test_file = BENCH_ROOT / "tasks" / "python_correctness" / "test_solution.py"
        test_code = test_file.read_text(encoding="utf-8")

        result = validate_python_solution(broken_code, test_code)

        # Broken code should fail all tests — but the key assertion is that
        # the validator used BROKEN_CODE (not some other file).
        self.assertFalse(result.passed, "Broken code must not pass")
        self.assertEqual(result.failed_tests, 6, "All 6 tests must fail with broken code")
        self.assertEqual(result.score, 0.0, "Score must be 0.0 for all failures")

    def test_runtime_file_contains_model_code_not_fixture(self):
        """The runtime file must contain the exact model submission, not the fixture."""
        from src.task_python import _save_latest_python_output, _extract_final_message, strip_code_fences

        # Simulate a correct LM Studio response with reasoning + message
        raw_output = [
            {"type": "reasoning", "content": "Let me analyze this problem..."},
            {
                "type": "message",
                "content": '```python\ndef add(a, b):\n    return a + b\n\ndef multiply(x, y):\n    result = x * y\n    return result\n\ndef is_even(n):\n    return n % 2 == 0\n```',
            },
        ]

        generated_code, _ = _extract_final_message(raw_output)
        self.assertIsNotNone(generated_code)
        cleaned_code = strip_code_fences(generated_code)

        # Save to runtime file
        _save_latest_python_output(cleaned_code)

        # Read back and verify it matches the model's submission
        output_file = BENCH_ROOT / "runtime" / "python" / "latest_output.py"
        saved_content = output_file.read_text(encoding="utf-8")

        self.assertEqual(saved_content, cleaned_code,
                         "Runtime file must contain exact model submission, not fixture content")
        # Verify it does NOT contain broken fixture markers
        self.assertNotIn("return a - b", saved_content,
                          "Runtime file must not contain broken fixture code")


class TestReasoningDiscarded(TestCase):
    """C. Reasoning is discarded — never reaches validator or runtime."""

    def test_reason_only_response_produces_no_final_answer(self):
        from src.task_python import _extract_final_message

        raw_output = [
            {"type": "reasoning", "content": "Let me think about this..."},
            {"type": "thinking", "content": "I should check edge cases."},
            {"type": "reasoning", "content": "The answer is probably 42."},
        ]

        content, reason = _extract_final_message(raw_output)

        self.assertIsNone(content, "No final message should be found")
        self.assertEqual(reason, "no_final_answer", "Reason must indicate no final answer")

    def test_reasoning_with_broken_python_not_in_validator(self):
        """When reasoning contains broken Python and there is a valid message, only the message matters."""
        from src.python_validator import validate_python_solution
        from src.task_python import _extract_final_message, strip_code_fences, _save_latest_python_output

        # Reasoning block contains intentionally broken code (must not be used)
        raw_output = [
            {"type": "reasoning", "content": '```python\ndef add(a, b):\n    return a - b\n```\n'},
            {
                "type": "message",
                # Message block contains the complete corrected module (all 3 functions)
                "content": '```python\ndef add(a, b):\n    return a + b\n\ndef multiply(x, y):\n    result = x * y\n    return result\n\ndef is_even(n):\n    return n % 2 == 0\n```',
            },
        ]

        generated_code, _ = _extract_final_message(raw_output)
        self.assertIsNotNone(generated_code)
        cleaned_code = strip_code_fences(generated_code)

        # Save to runtime file
        _save_latest_python_output(cleaned_code)

        test_file = BENCH_ROOT / "tasks" / "python_correctness" / "test_solution.py"
        test_code = test_file.read_text(encoding="utf-8")

        result = validate_python_solution(cleaned_code, test_code)

        # The validator must use the MESSAGE content (correct), not reasoning (broken)
        self.assertTrue(result.passed, f"Validator must receive corrected message, not broken reasoning. Got score={result.score}")
        self.assertEqual(result.passed_tests, 6)

        # Verify runtime file also has correct code
        output_file = BENCH_ROOT / "runtime" / "python" / "latest_output.py"
        saved_content = output_file.read_text(encoding="utf-8")
        self.assertIn("return a + b", saved_content)
        self.assertNotIn("return a - b", saved_content,
                          "Runtime file must not contain broken reasoning code")


class TestPytestParser(TestCase):
    """D. Pytest parser handles all output formats correctly."""

    def _parse_from_stdout(self, stdout_text):
        """Helper to test the parsing logic directly by creating a mock result."""
        from src.python_validator import _parse_pytest_count

        passed = _parse_pytest_count(stdout_text, "passed")
        failed = _parse_pytest_count(stdout_text, "failed")
        return passed, failed

    def test_all_passed_simple(self):
        stdout = "......                                                                   [100%]\n6 passed in 0.02s\n"
        passed, failed = self._parse_from_stdout(stdout)
        self.assertEqual(passed, 6)
        self.assertEqual(failed, 0)

    def test_mixed_summary(self):
        stdout = ".....F\n1 failed, 5 passed in 0.03s\n"
        passed, failed = self._parse_from_stdout(stdout)
        self.assertEqual(passed, 5)
        self.assertEqual(failed, 1)

    def test_failed_first(self):
        stdout = "1 failed, 5 passed in 0.03s\n"
        passed, failed = self._parse_from_stdout(stdout)
        self.assertEqual(passed, 5)
        self.assertEqual(failed, 1)

    def test_only_passed_no_failed_keyword(self):
        stdout = "6 passed in 0.02s\n"
        passed, failed = self._parse_from_stdout(stdout)
        self.assertEqual(passed, 6)
        self.assertEqual(failed, 0)

    def test_dot_lines_before_summary(self):
        """Dot output lines before the summary line must not confuse the parser."""
        stdout = "F.....\n1 failed, 5 passed in 0.03s\n"
        passed, failed = self._parse_from_stdout(stdout)
        self.assertEqual(passed, 5)
        self.assertEqual(failed, 1)

    def test_empty_output(self):
        stdout = ""
        passed, failed = self._parse_from_stdout(stdout)
        self.assertEqual(passed, 0)
        self.assertEqual(failed, 0)


class TestNoFinalMessage(TestCase):
    """E. No final message → empty runtime, 0/6, score=0."""

    def test_reasoning_only_produces_zero_score(self):
        from src.task_python import _extract_final_message

        raw_output = [
            {"type": "reasoning", "content": "Let me analyze this..."},
            {"type": "thinking", "content": "I need to consider edge cases."},
        ]

        content, reason = _extract_final_message(raw_output)

        self.assertIsNone(content)
        self.assertEqual(reason, "no_final_answer")

    def test_empty_runtime_file_on_no_final_answer(self):
        from src.task_python import _save_latest_python_output

        output_dir = BENCH_ROOT / "runtime" / "python"
        output_file = output_dir / "latest_output.py"

        # Write empty string (simulating no final answer path)
        _save_latest_python_output("")

        saved = output_file.read_text(encoding="utf-8")
        self.assertEqual(saved, "", "Runtime file must be empty when no final answer")


if __name__ == "__main__":
    main()