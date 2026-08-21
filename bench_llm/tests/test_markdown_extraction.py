"""Focused tests for task_markdown._extract_final_message trailing-newline preservation.

Proves that extraction preserves model-produced final newlines (MD047 compliance)
while still stripping leading whitespace from reasoning blocks.
"""

import pytest

from src.task_markdown import _extract_final_message


# ------------------------------------------------------------------
# A. Input ending with "\n" → extracted Markdown must still end with "\n"
# ------------------------------------------------------------------

class TestTrailingNewlinePreserved:
    """When the model returns content ending in \\n, extraction must preserve it."""

    def test_plain_string_with_trailing_newline(self):
        raw = "# Hello\n## World\n"
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert result.endswith("\n"), "trailing newline must be preserved for MD047"
        assert result == "# Hello\n## World\n"

    def test_dict_output_key_with_trailing_newline(self):
        raw = {"output": "# Hello\n"}
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert result.endswith("\n")
        assert result == "# Hello\n"

    def test_dict_text_key_with_trailing_newline(self):
        raw = {"text": "## Section\n"}
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert result.endswith("\n")
        assert result == "## Section\n"

    def test_dict_nested_text_with_trailing_newline(self):
        raw = {"output": {"text": "### Title\n"}}
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert result.endswith("\n")
        assert result == "### Title\n"

    def test_list_of_parts_with_trailing_newline(self):
        """Content as list of strings (common LM Studio endpoint shape).
        Parts are joined with \\n, so each part's trailing newline is preserved."""
        raw = [{"type": "message", "content": ["# Hello\n", "## World\n"]}]
        result, reason = _extract_final_message(raw)
        assert reason == ""
        # join adds one \n between parts; each part keeps its own trailing \n
        assert result.endswith("\n")
        assert result == "# Hello\n\n## World\n"

    def test_list_of_parts_with_trailing_newline_after_join(self):
        raw = [{"type": "message", "content": ["first line\n"]}]
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert result.endswith("\n")


# ------------------------------------------------------------------
# B. Input without final "\n" → extracted Markdown must NOT gain one
# ------------------------------------------------------------------

class TestNoSpuriousNewlineAppended:
    """When the model returns content WITHOUT a trailing newline, extraction
    must not append one — the validator should see what the model actually produced."""

    def test_plain_string_without_trailing_newline(self):
        raw = "# Hello\n## World"
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert not result.endswith("\n"), "must not append newline when absent"
        assert result == "# Hello\n## World"

    def test_dict_output_key_without_trailing_newline(self):
        raw = {"output": "# Hello"}
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert not result.endswith("\n")
        assert result == "# Hello"

    def test_list_of_parts_without_trailing_newline(self):
        raw = [{"type": "message", "content": ["# Hello"]}]
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert not result.endswith("\n")
        assert result == "# Hello"


# ------------------------------------------------------------------
# C. Ordinary Markdown content is otherwise unchanged
# ------------------------------------------------------------------

class TestContentUnchanged:
    """Extraction must not alter the actual markdown content — only trim leading
    whitespace from reasoning blocks."""

    def test_leading_blank_lines_stripped(self):
        """Leading blank lines from reasoning should be removed."""
        raw = "\n\n\n# Hello\n## World\n"
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert not result.startswith("\n"), "leading newlines must be stripped"
        assert result == "# Hello\n## World\n"

    def test_leading_spaces_stripped(self):
        raw = "   \n# Hello\n"
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert not result.startswith(" "), "leading spaces must be stripped"
        assert result == "# Hello\n"

    def test_code_blocks_preserved(self):
        raw = """```python
x = 1
```"""
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert "```python" in result
        assert "x = 1" in result
        assert "```" in result

    def test_links_preserved(self):
        raw = "[docs](https://example.com)\n## API"
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert "[docs](https://example.com)" in result

    def test_empty_string_returns_empty_with_no_reason(self):
        """Plain empty string path returns ('', '') — this is pre-existing behavior.
        The None-return path only applies to dict/list fallback branches."""
        raw = ""
        result, reason = _extract_final_message(raw)
        assert result == ""
        assert reason == ""

    def test_dict_no_matching_key_returns_joined_values(self):
        """Fallback joins all string values when no matching key found.
        Returns content with 'no_final_answer' reason (pre-existing behavior)."""
        raw = {"thinking": "never mind", "other": "data"}
        result, reason = _extract_final_message(raw)
        # Falls through to fallback join — both values are strings → joined
        assert reason == "no_final_answer"  # pre-existing: fallback always uses this reason
        assert result == "never mind\ndata"

    def test_list_without_message_block_returns_none(self):
        raw = [{"type": "reasoning", "content": "thinking"}]
        result, reason = _extract_final_message(raw)
        assert result is None
        assert reason == "no_final_answer"


# ------------------------------------------------------------------
# D. Multiple message blocks — last one wins (existing behaviour preserved)
# ------------------------------------------------------------------

class TestMultipleMessageBlocks:
    """Only the last 'message' block should be returned."""

    def test_last_message_block_wins(self):
        raw = [
            {"type": "reasoning", "content": "thinking first"},
            {"type": "message", "content": "# Answer A\n"},
            {"type": "reasoning", "content": "more thinking"},
            {"type": "message", "content": "# Answer B\n"},
        ]
        result, reason = _extract_final_message(raw)
        assert reason == ""
        assert result == "# Answer B\n"

    def test_last_message_block_preserves_trailing_newline(self):
        raw = [
            {"type": "message", "content": "## First\n"},
            {"type": "message", "content": "## Second\n"},
        ]
        result, reason = _extract_final_message(raw)
        assert result == "## Second\n"