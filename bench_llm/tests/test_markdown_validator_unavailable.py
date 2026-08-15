"""Regression tests for markdownlint validator unavailability handling.

Ensures that when no markdownlint implementation is available, the validator
does NOT return a false zero-error validation result.

These tests mock both CLI and python-markdownlint as unavailable to simulate
the environment where neither tool is installed.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch
from unittest import TestCase, main

from src.markdownlint_validator import (
    MarkdownLintResult,
    MarkdownLintValidator,
    run_markdownlint_benchmark,
)


class TestMarkdownLintResultUnavailable(TestCase):
    """Test the MarkdownLintResult.unavailable() factory method."""

    def test_unavailable_result_has_correct_status(self):
        """unavailable() must set status to STATUS_UNAVAILABLE."""
        result = MarkdownLintResult.unavailable("test message")
        self.assertEqual(result.status, MarkdownLintResult.STATUS_UNAVAILABLE)

    def test_unavailable_result_has_error_message(self):
        """unavailable() must include a clear error message."""
        msg = "markdownlint is not available"
        result = MarkdownLintResult.unavailable(msg)
        self.assertIn("not available", result.error_message.lower())

    def test_unavailable_is_available_false(self):
        """unavailable() must set is_available to False."""
        result = MarkdownLintResult.unavailable("test")
        self.assertFalse(result.is_available)

    def test_completed_result_is_available_true(self):
        """A normal completed result must have is_available=True."""
        result = MarkdownLintResult(
            violations=[], output="", command_used="markdownlint-cli"
        )
        self.assertTrue(result.is_available)

    def test_unavailable_to_dict_includes_status(self):
        """to_dict() on unavailable result must include status=unavailable."""
        result = MarkdownLintResult.unavailable("test")
        d = result.to_dict()
        self.assertEqual(d["status"], "unavailable")
        self.assertIn("error_message", d)

    def test_unavailable_count_is_zero_but_not_a_valid_pass(self):
        """Unavailable results have count=0 but is_available=False — must not be treated as pass."""
        result = MarkdownLintResult.unavailable("test")
        self.assertEqual(result.count, 0)
        self.assertFalse(result.is_available)


class TestValidatorUnavailability(TestCase):
    """Test that validate_file returns unavailable when no tool is present."""

    def _mock_both_unavailable(self):
        """Return a patch set that disables both CLI and python-markdownlint."""
        return patch.multiple(
            "src.markdownlint_validator",
            _find_markdownlint=return_none,
            _has_python_markdownlint=return_false,
        )

    def test_validate_file_returns_unavailable_when_no_tool(self):
        """validate_file() must NOT return a zero-error completed result when no tool is available."""
        with self._mock_both_unavailable():
            validator = MarkdownLintValidator()
            # Create a temp file to validate
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()
            ) as f:
                f.write("# Title\n\nSome text.\n")
                f.flush()
                path = Path(f.name)

            try:
                result = validator.validate_file(path)
                self.assertFalse(result.is_available)
                self.assertEqual(result.status, MarkdownLintResult.STATUS_UNAVAILABLE)
                self.assertIn("not available", result.error_message.lower())
            finally:
                path.unlink(missing_ok=True)

    def test_validate_string_returns_unavailable_when_no_tool(self):
        """validate_string() must also propagate unavailability."""
        with self._mock_both_unavailable():
            validator = MarkdownLintValidator()
            result = validator.validate_string("# Title\n\nSome text.\n")
            self.assertFalse(result.is_available)
            self.assertEqual(result.status, MarkdownLintResult.STATUS_UNAVAILABLE)


class TestBenchmarkUnavailability(TestCase):
    """Test that run_markdownlint_benchmark handles unavailability correctly."""

    def _mock_both_unavailable(self):
        return patch.multiple(
            "src.markdownlint_validator",
            _find_markdownlint=return_none,
            _has_python_markdownlint=return_false,
        )

    def test_benchmark_returns_no_score_when_unavailable(self):
        """run_markdownlint_benchmark must NOT return score=1.0 when validator is unavailable."""
        with self._mock_both_unavailable():
            result = run_markdownlint_benchmark("# Title", "## Fixed")
            self.assertIsNone(result["score"])
            self.assertFalse(result["passed"])
            self.assertIsNone(result["initial_errors"])
            self.assertIsNone(result["final_errors"])
            self.assertIn("validator_available", result)
            self.assertFalse(result["validator_available"])

    def test_benchmark_includes_error_message_when_unavailable(self):
        """run_markdownlint_benchmark must include a clear error message when unavailable."""
        with self._mock_both_unavailable():
            result = run_markdownlint_benchmark("# Title", "## Fixed")
            self.assertIn("error", result)
            self.assertIn("not available", result["error"].lower())


def return_none():
    """Helper for mocking _find_markdownlint."""
    return None


def return_false():
    """Helper for mocking _has_python_markdownlint."""
    return False


if __name__ == "__main__":
    main()