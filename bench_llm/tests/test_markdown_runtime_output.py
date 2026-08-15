"""Regression tests for Act M4.5 — save latest markdown benchmark output."""

import os
import tempfile
from pathlib import Path
from unittest import TestCase, main


class TestSaveLatestMarkdownOutput(TestCase):
    """Test _save_latest_markdown_output behavior."""

    def _get_runtime_path(self) -> Path:
        return Path(__file__).parent.parent / "runtime" / "markdown"

    def test_save_function_exists(self):
        from src.task_markdown import _save_latest_markdown_output
        self.assertTrue(callable(_save_latest_markdown_output))

    def test_file_created_on_first_write(self):
        """Test that the runtime file is created on first write."""
        from src.task_markdown import _save_latest_markdown_output

        output_dir = self._get_runtime_path()
        output_file = output_dir / "latest_output.md"

        # Clean up if exists from previous runs
        if output_file.exists():
            output_file.unlink()

        content = "# Test Document\n\nThis is a test.\n"
        _save_latest_markdown_output(content)

        self.assertTrue(output_file.exists(), "runtime/markdown/latest_output.md should be created")
        self.assertEqual(output_file.read_text(encoding="utf-8"), content)

    def test_second_run_overwrites_first(self):
        """Test that a second run overwrites the first content."""
        from src.task_markdown import _save_latest_markdown_output

        output_dir = self._get_runtime_path()
        output_file = output_dir / "latest_output.md"

        # First write
        first_content = "# First Run\n\nContent A.\n"
        _save_latest_markdown_output(first_content)
        self.assertEqual(output_file.read_text(encoding="utf-8"), first_content)

        # Second write — should overwrite, not append
        second_content = "# Second Run\n\nContent B.\n"
        _save_latest_markdown_output(second_content)

        saved = output_file.read_text(encoding="utf-8")
        self.assertEqual(saved, second_content)
        self.assertNotIn("Content A", saved, "First content should not be in file after overwrite")
        self.assertNotIn("First Run", saved, "File should contain only the latest content")

    def test_saved_contents_match_generated_response(self):
        """Test that saved contents exactly match what was passed to _save_latest_markdown_output."""
        from src.task_markdown import _save_latest_markdown_output

        output_dir = self._get_runtime_path()
        output_file = output_dir / "latest_output.md"

        # Simulate exact model output (including reasoning text)
        generated_response = (
            "Here's a thinking process:\n\n"
            "1. **Analyze User Input:**\n   - Task: Fix Markdown.\n\n"
            "2. **Identify Errors:**\n   - MD040 missing language\n   - MD034 bare URL\n\n"
            "```python\nprint('hello')\n```\n\n"
            "[Link](http://example.com)\n"
        )

        _save_latest_markdown_output(generated_response)
        saved = output_file.read_text(encoding="utf-8")

        self.assertEqual(saved, generated_response, "Saved file must exactly match generated response")

    def test_parent_dirs_created_automatically(self):
        """Test that parent directories are created if missing."""
        from src.task_markdown import _save_latest_markdown_output

        output_dir = self._get_runtime_path()

        # Remove the entire runtime directory to simulate fresh state
        if output_dir.exists():
            for p in output_dir.rglob("*"):
                if p.is_file():
                    p.unlink()
            (output_dir / "latest_output.md").unlink(missing_ok=True)
            try:
                output_dir.rmdir()
            except OSError:
                pass

        content = "# Fresh Start\n"
        _save_latest_markdown_output(content)

        self.assertTrue(output_dir.exists(), "Parent directory should be created automatically")
        self.assertTrue((output_dir / "latest_output.md").exists())


if __name__ == "__main__":
    main()