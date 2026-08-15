"""Regression tests for Act M4.6 — avoid scoring markdown reasoning output."""

import tempfile
from pathlib import Path
from unittest import TestCase, main


class TestExtractFinalMessage(TestCase):
    """Test _extract_final_message behavior (Act M4.6)."""

    def _get_runtime_path(self) -> Path:
        return Path(__file__).parent.parent / "runtime" / "markdown"

    # ---- reasoning + message -> only message scored ----

    def test_reasoning_plus_message_returns_only_message(self):
        """When output has reasoning followed by a final message, extract only the message."""
        from src.task_markdown import _extract_final_message

        raw_output = [
            {"type": "reasoning", "content": "Let me think about this..."},
            {"type": "message", "content": "# Fixed Document\n\nHere is the corrected markdown."},
        ]
        content, reason = _extract_final_message(raw_output)

        self.assertEqual(reason, "")
        self.assertIsNotNone(content)
        self.assertIn("# Fixed Document", content)
        self.assertNotIn("Let me think", content)

    def test_reasoning_plus_message_reverse_order(self):
        """Message before reasoning — should still pick the LAST message block."""
        from src.task_markdown import _extract_final_message

        raw_output = [
            {"type": "message", "content": "# Earlier Message"},
            {"type": "reasoning", "content": "Additional thoughts..."},
            {"type": "message", "content": "# Final Answer\n\nThis is the corrected output."},
        ]
        content, reason = _extract_final_message(raw_output)

        self.assertEqual(reason, "")
        self.assertIsNotNone(content)
        self.assertIn("# Final Answer", content)
        self.assertNotIn("Earlier Message", content)

    # ---- reasoning-only -> no_final_answer ----

    def test_reasoning_only_returns_no_final_answer(self):
        """When output contains only reasoning blocks, return (None, 'no_final_answer')."""
        from src.task_markdown import _extract_final_message

        raw_output = [
            {"type": "reasoning", "content": "Let me analyze the document..."},
            {"type": "thinking", "content": "I should check for markdown issues."},
        ]
        content, reason = _extract_final_message(raw_output)

        self.assertIsNone(content)
        self.assertEqual(reason, "no_final_answer")

    def test_empty_list_returns_no_final_answer(self):
        """Empty list has no message blocks."""
        from src.task_markdown import _extract_final_message

        content, reason = _extract_final_message([])

        self.assertIsNone(content)
        self.assertEqual(reason, "no_final_answer")

    # ---- normal string response still works ----

    def test_plain_string_returns_stripped_content(self):
        """A plain string should be returned stripped."""
        from src.task_markdown import _extract_final_message

        content, reason = _extract_final_message("  # Title\n\nSome text.  ")

        self.assertEqual(content, "# Title\n\nSome text.")
        self.assertEqual(reason, "")

    def test_dict_with_output_key(self):
        """Dict with 'output' key should extract the string value."""
        from src.task_markdown import _extract_final_message

        raw = {"output": "# Dict Output\n\nContent here."}
        content, reason = _extract_final_message(raw)

        self.assertEqual(content, "# Dict Output\n\nContent here.")
        self.assertEqual(reason, "")

    def test_dict_with_text_key(self):
        """Dict with 'text' key should extract the string value."""
        from src.task_markdown import _extract_final_message

        raw = {"text": "# Text Key\n\nFrom text."}
        content, reason = _extract_final_message(raw)

        self.assertEqual(content, "# Text Key\n\nFrom text.")
        self.assertEqual(reason, "")

    # ---- runtime file behavior for no final message ----

    def test_runtime_file_overwritten_with_empty_on_no_final(self):
        """When there is no final message, the runtime file should be overwritten with empty string."""
        from src.task_markdown import _extract_final_message, _save_latest_markdown_output

        output_dir = self._get_runtime_path()
        output_file = output_dir / "latest_output.md"

        # Simulate reasoning-only response
        raw_output = [
            {"type": "reasoning", "content": "Thinking..."},
        ]
        content, reason = _extract_final_message(raw_output)

        self.assertIsNone(content)
        self.assertEqual(reason, "no_final_answer")

        # When no final message exists, save empty string to runtime file
        if not content:
            _save_latest_markdown_output("")

        saved = output_file.read_text(encoding="utf-8")
        self.assertEqual(saved, "", "Runtime file should contain empty string when no final answer")


class TestNoFakeMarkdownlintErrorCount(TestCase):
    """Ensure reasoning-only responses do NOT produce fake markdownlint error counts."""

    def test_reasoning_only_does_not_produce_error_count(self):
        """Reasoning-only output must not be linted — it should short-circuit before validation."""
        from src.task_markdown import _extract_final_message

        # Simulate the exact reasoning-only response that caused 11 errors previously
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


if __name__ == "__main__":
    main()