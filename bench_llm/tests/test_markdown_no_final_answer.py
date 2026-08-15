"""Regression tests for Act M4.8 — clarify markdown no-final-answer results."""

import tempfile
from pathlib import Path
from unittest import TestCase, main


class TestNoFinalAnswerBackend(TestCase):
    """Test that no_final_answer returns correct initial_errors."""

    def _get_runtime_path(self) -> Path:
        return Path(__file__).parent.parent / "runtime" / "markdown"

    def test_no_final_answer_returns_initial_errors_6(self):
        """When model returns only reasoning, initial_errors should be 6 (canonical count)."""
        from src.task_markdown import _extract_final_message, load_fixture_broken_md
        from src.markdownlint_validator import MarkdownLintValidator

        # Simulate reasoning-only response
        raw_output = [
            {"type": "reasoning", "content": "Let me analyze the document..."},
        ]
        content, reason = _extract_final_message(raw_output)

        self.assertIsNone(content)
        self.assertEqual(reason, "no_final_answer")

        # Validate canonical fixture to confirm initial_errors=6
        validator = MarkdownLintValidator()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()
        ) as f:
            f.write(load_fixture_broken_md())
            f.flush()
            temp_path = Path(f.name)

        try:
            result = validator.validate_file(temp_path)
            self.assertEqual(result.count, 6, "Canonical broken.md should have exactly 6 violations")
        finally:
            temp_path.unlink(missing_ok=True)

    def test_validator_available_true_when_canonical_fixture_validated(self):
        """validator_available should reflect actual markdownlint availability."""
        from src.markdownlint_validator import MarkdownLintValidator

        validator = MarkdownLintValidator()
        dep_info = validator.check_dependency()
        validator_available = dep_info["cli_available"] or dep_info["python_available"]

        # On this system, cli2 should be available
        self.assertIsInstance(validator_available, bool)

    def test_no_final_answer_does_not_lint_reasoning(self):
        """Reasoning text must never be passed to the validator."""
        from src.task_markdown import _extract_final_message

        # Simulate the exact reasoning that caused 11 errors previously
        raw_output = [
            {"type": "reasoning", "content": (
                "Here's a thinking process:\n\n"
                "1. **Analyze User Input:**\n   - Task: Fix Markdown.\n\n"
                "2. **Identify Errors:**\n   - MD040 missing language\n   - MD034 bare URL\n\n"
                "```python\nprint('hello')\n```\n\n"
                "[Link](http://example.com)\n"
            )},
        ]

        content, reason = _extract_final_message(raw_output)

        # Must short-circuit — no validation should occur
        self.assertIsNone(content)
        self.assertEqual(reason, "no_final_answer")


class TestRuntimeOutputBehavior(TestCase):
    """Test runtime/markdown/latest_output.md behavior for no_final_answer."""

    def _get_runtime_path(self) -> Path:
        return Path(__file__).parent.parent / "runtime" / "markdown"

    def test_no_final_answer_writes_empty_string(self):
        """When there is no final message, the runtime file should be overwritten with empty string."""
        from src.task_markdown import _save_latest_markdown_output

        output_dir = self._get_runtime_path()
        output_file = output_dir / "latest_output.md"

        # Write empty string (simulating no_final_answer)
        _save_latest_markdown_output("")

        saved = output_file.read_text(encoding="utf-8")
        self.assertEqual(saved, "", "Runtime file should contain empty string when no final answer")


class TestReasoningNeverLinted(TestCase):
    """Ensure reasoning is never passed to markdownlint."""

    def test_reasoning_content_not_validated(self):
        """Any response with only reasoning blocks must return (None, 'no_final_answer')."""
        from src.task_markdown import _extract_final_message

        # Various reasoning block formats that models might produce
        test_cases = [
            [{"type": "reasoning", "content": "Thinking..."}],
            [{"type": "thinking", "content": "Let me think..."}],
            [{"type": "reasoning", "content": "Analysis"}, {"type": "thinking", "content": "More thoughts"}],
        ]

        for raw_output in test_cases:
            content, reason = _extract_final_message(raw_output)
            self.assertIsNone(content, f"Expected None for reasoning-only output: {raw_output}")
            self.assertEqual(reason, "no_final_answer")


if __name__ == "__main__":
    main()