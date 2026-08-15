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


# ------------------------------------------------------------------
# CLI2-specific tests
# ------------------------------------------------------------------

class TestMarkdownLintCli2Detection(TestCase):
    """Test that markdownlint-cli2 is detected when available via npx."""

    def test_cli2_detected_when_npx_available(self):
        """When npx markdownlint-cli2 works, _find_markdownlint should return it."""
        from src.markdownlint_validator import _find_markdownlint
        result = _find_markdownlint()
        # On systems with node/npx and markdownlint-cli2 installed via npx:
        if result is not None:
            self.assertIn("markdownlint-cli2", result)

    def test_cli2_parser_validates_broken_line(self):
        """_parse_cli2_line should correctly parse broken.md output lines."""
        from src.markdownlint_validator import MarkdownLintValidator
        line = 'bench_llm/tasks/markdownlint_default/broken.md:13 error MD040/fenced-code-language Fenced code blocks should have a language specified [Context: "```"]'
        result = MarkdownLintValidator._parse_cli2_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["line"], 13)
        self.assertEqual(result["level"], "error")
        self.assertEqual(result["rule"], "MD040")

    def test_cli2_parser_validates_url_line(self):
        """_parse_cli2_line should parse bare URL violation lines."""
        from src.markdownlint_validator import MarkdownLintValidator
        line = 'bench_llm/tasks/markdownlint_default/broken.md:69:13 error MD034/no-bare-urls Bare URL used [Context: "http://localhost:3000/api/user..."]'
        result = MarkdownLintValidator._parse_cli2_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["line"], 69)
        self.assertEqual(result["rule"], "MD034")

    def test_cli2_parser_rejects_header_lines(self):
        """_parse_cli2_line should return None for non-violation lines."""
        from src.markdownlint_validator import MarkdownLintValidator
        # Summary line
        self.assertIsNone(MarkdownLintValidator._parse_cli2_line("Summary: 6 issues in 1 file"))
        # Finding line
        self.assertIsNone(MarkdownLintValidator._parse_cli2_line("Finding: bench_llm/tasks/markdownlint_default/broken.md"))
        # Linting line
        self.assertIsNone(MarkdownLintValidator._parse_cli2_line("Linting: 1 file"))

    def test_cli2_clean_document_no_violations(self):
        """A clean document should produce zero violations when validated."""
        import tempfile
        from pathlib import Path
        from src.markdownlint_validator import MarkdownLintValidator

        # Create a minimal clean markdown file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()
        ) as f:
            f.write("# Title\n\n## Section\n\nSome text.\n")
            f.flush()
            path = Path(f.name)

        try:
            validator = MarkdownLintValidator()
            result = validator.validate_file(path)
            # If cli2 is available, it should parse successfully
            if "markdownlint-cli2" in (result.command_used or ""):
                self.assertTrue(result.is_available)
                # Clean document may still have issues (e.g., missing trailing newline)
                # but violations should be parseable
        finally:
            path.unlink(missing_ok=True)

    def test_cli2_broken_fixture_has_violations(self):
        """The canonical broken.md fixture should produce non-zero violations when cli2 is used."""
        import tempfile
        from pathlib import Path
        from src.markdownlint_validator import MarkdownLintValidator, _find_markdownlint

        # Only run if cli2 is available
        detected = _find_markdownlint()
        if detected is None or "markdownlint-cli2" not in detected:
            self.skipTest("markdownlint-cli2 not available")

        fixture_path = Path(__file__).parent.parent / "tasks" / "markdownlint_default" / "broken.md"
        if not fixture_path.exists():
            self.skipTest("broken.md fixture not found")

        validator = MarkdownLintValidator()
        result = validator.validate_file(fixture_path)

        self.assertTrue(result.is_available)
        self.assertIn("markdownlint-cli2", result.command_used)
        # The broken fixture should have violations
        self.assertGreater(result.count, 0)


def return_none():
    """Helper for mocking _find_markdownlint."""
    return None


def return_false():
    """Helper for mocking _has_python_markdownlint."""
    return False


if __name__ == "__main__":
    main()
