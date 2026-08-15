"""Regression tests for Act P5.1 — Python runtime submission trace.

Tests ensure that:
1. reasoning + message → message only
2. multiple messages → last message selected
3. reasoning only → empty runtime file
4. runtime file overwritten each run
5. saved runtime text == validator input exactly
6. no str(dict) contamination
7. existing string/dict/list response formats remains supported
"""

import tempfile
from pathlib import Path
from unittest import TestCase, main


class TestExtractFinalMessage(TestCase):
    """Test _extract_final_message behavior (Act P5.1)."""

    def test_reasoning_plus_message_returns_only_message(self):
        """When output has reasoning followed by a final message, extract only the message."""
        from src.task_python import _extract_final_message

        raw_output = [
            {"type": "reasoning", "content": "Let me think about this..."},
            {"type": "message", "content": "def process_data(nums: list[int]) -> dict:\n    return {'result': nums}"},
        ]
        content, reason = _extract_final_message(raw_output)

        self.assertEqual(reason, "")
        self.assertIsNotNone(content)
        self.assertIn("def process_data", content)
        self.assertNotIn("Let me think", content)

    def test_multiple_messages_last_selected(self):
        """When there are multiple message blocks, the LAST one should be selected."""
        from src.task_python import _extract_final_message

        raw_output = [
            {"type": "reasoning", "content": "First thoughts..."},
            {"type": "message", "content": "# First draft\npass"},
            {"type": "reasoning", "content": "Additional analysis..."},
            {"type": "message", "content": "# Final answer\ndef solve():\n    return 42"},
        ]
        content, reason = _extract_final_message(raw_output)

        self.assertEqual(reason, "")
        self.assertIsNotNone(content)
        self.assertIn("def solve", content)
        self.assertNotIn("# First draft", content)
        self.assertNotIn("First thoughts", content)

    def test_reasoning_only_returns_no_final_answer(self):
        """When output contains only reasoning blocks, return (None, 'no_final_answer')."""
        from src.task_python import _extract_final_message

        raw_output = [
            {"type": "reasoning", "content": "Let me analyze the problem..."},
            {"type": "thinking", "content": "I should check for edge cases."},
        ]
        content, reason = _extract_final_message(raw_output)

        self.assertIsNone(content)
        self.assertEqual(reason, "no_final_answer")

    def test_empty_list_returns_no_final_answer(self):
        """Empty list has no message blocks."""
        from src.task_python import _extract_final_message

        content, reason = _extract_final_message([])

        self.assertIsNone(content)
        self.assertEqual(reason, "no_final_answer")

    def test_plain_string_returns_stripped_content(self):
        """A plain string should be returned stripped."""
        from src.task_python import _extract_final_message

        content, reason = _extract_final_message("  def hello():\n    pass  ")

        self.assertEqual(content, "def hello():\n    pass")
        self.assertEqual(reason, "")

    def test_dict_with_output_key(self):
        """Dict with 'output' key should extract the string value."""
        from src.task_python import _extract_final_message

        raw = {"output": "def solve():\n    return True"}
        content, reason = _extract_final_message(raw)

        self.assertEqual(content, "def solve():\n    return True")
        self.assertEqual(reason, "")

    def test_dict_with_text_key(self):
        """Dict with 'text' key should extract the string value."""
        from src.task_python import _extract_final_message

        raw = {"text": "class Counter:\n    pass"}
        content, reason = _extract_final_message(raw)

        self.assertEqual(content, "class Counter:\n    pass")
        self.assertEqual(reason, "")

    def test_dict_with_response_key(self):
        """Dict with 'response' key should extract the string value."""
        from src.task_python import _extract_final_message

        raw = {"response": "# Response format\ndef foo(): pass"}
        content, reason = _extract_final_message(raw)

        self.assertEqual(content, "# Response format\ndef foo(): pass")
        self.assertEqual(reason, "")

    def test_dict_with_content_key(self):
        """Dict with 'content' key should extract the string value."""
        from src.task_python import _extract_final_message

        raw = {"content": "def bar(): return 1"}
        content, reason = _extract_final_message(raw)

        self.assertEqual(content, "def bar(): return 1")
        self.assertEqual(reason, "")


class TestSaveLatestPythonOutput(TestCase):
    """Test _save_latest_python_output behavior (Act P5.1)."""

    def _get_runtime_path(self) -> Path:
        return Path(__file__).parent.parent / "runtime" / "python"

    def test_save_function_exists(self):
        from src.task_python import _save_latest_python_output
        self.assertTrue(callable(_save_latest_python_output))

    def test_file_created_on_first_write(self):
        """Test that the runtime file is created on first write."""
        from src.task_python import _save_latest_python_output

        output_dir = self._get_runtime_path()
        output_file = output_dir / "latest_output.py"

        # Clean up if exists from previous runs
        if output_file.exists():
            output_file.unlink()

        content = 'def process_data(nums):\n    return [x for x in nums if x % 2 == 0]\n'
        _save_latest_python_output(content)

        self.assertTrue(output_file.exists(), "runtime/python/latest_output.py should be created")
        self.assertEqual(output_file.read_text(encoding="utf-8"), content)

    def test_second_run_overwrites_first(self):
        """Test that a second run overwrites the first content."""
        from src.task_python import _save_latest_python_output

        output_dir = self._get_runtime_path()
        output_file = output_dir / "latest_output.py"

        # First write
        first_content = 'def first():\n    pass\n'
        _save_latest_python_output(first_content)
        self.assertEqual(output_file.read_text(encoding="utf-8"), first_content)

        # Second write — should overwrite, not append
        second_content = 'def second():\n    return 42\n'
        _save_latest_python_output(second_content)

        saved = output_file.read_text(encoding="utf-8")
        self.assertEqual(saved, second_content)
        self.assertNotIn("first", saved, "First content should not be in file after overwrite")
        self.assertNotIn("def first", saved, "File should contain only the latest content")

    def test_parent_dirs_created_automatically(self):
        """Test that parent directories are created if missing."""
        from src.task_python import _save_latest_python_output

        output_dir = self._get_runtime_path()

        # Remove the entire runtime directory to simulate fresh state
        if output_dir.exists():
            for p in output_dir.rglob("*"):
                if p.is_file():
                    p.unlink()
            (output_dir / "latest_output.py").unlink(missing_ok=True)
            try:
                output_dir.rmdir()
            except OSError:
                pass

        content = 'def fresh_start():\n    pass\n'
        _save_latest_python_output(content)

        self.assertTrue(output_dir.exists(), "Parent directory should be created automatically")
        self.assertTrue((output_dir / "latest_output.py").exists())


class TestRuntimeValidatorInputEquality(TestCase):
    """Test that saved runtime text == validator input exactly."""

    def test_saved_runtime_text_equals_validator_input(self):
        """The exact text in latest_output.py must match what validate_python_solution() receives."""
        from src.task_python import _extract_final_message, strip_code_fences, _save_latest_python_output
        from src.python_validator import validate_python_solution

        # Simulate LM Studio response with reasoning + message
        raw_output = [
            {"type": "reasoning", "content": "Let me solve this step by step..."},
            {
                "type": "message",
                "content": '```python\ndef process_data(nums: list[int], operation: str) -> dict:\n    """Process the input list."""\n    if operation == "even":\n        return {"result": [x for x in nums if x % 2 == 0]}\n    return {"result": []}\n```',
            },
        ]

        # Extract final message (what task runner does)
        generated_code, _ = _extract_final_message(raw_output)
        self.assertIsNotNone(generated_code)

        # Strip code fences (what task runner does)
        cleaned_code = strip_code_fences(generated_code)

        # Save to runtime file (what task runner does)
        _save_latest_python_output(cleaned_code)

        # Validate (what task runner does)
        test_code = '''
def test_process_data_even():
    from solution import process_data
    result = process_data([1, 2, 3, 4], "even")
    assert result == {"result": [2, 4]}
'''
        validation_result = validate_python_solution(cleaned_code, test_code)

        # Read runtime file
        output_file = Path(__file__).parent.parent / "runtime" / "python" / "latest_output.py"
        runtime_content = output_file.read_text(encoding="utf-8")

        # CRITICAL: runtime content must match exactly what validator received
        self.assertEqual(runtime_content, cleaned_code,
                         "Runtime file must contain the exact code passed to validate_python_solution()")


class TestNoStrDictContamination(TestCase):
    """Ensure no str(dict) contamination in final output."""

    def test_no_str_dict_contamination(self):
        """When response is a list of dicts, we must NOT produce \"{'type': 'message', ...}\" strings."""
        from src.task_python import _extract_final_message

        # This is the EXACT shape that would cause str(dict) contamination if wrong
        raw_output = [
            {"type": "reasoning", "content": "Thinking process..."},
            {"type": "message", "content": "def correct_solution():\n    return 'yes'\n"},
        ]

        content, reason = _extract_final_message(raw_output)

        self.assertEqual(reason, "")
        self.assertIsNotNone(content)
        # Must contain the actual Python code
        self.assertIn("def correct_solution", content)
        # Must NOT contain dict representation artifacts
        self.assertNotIn("{'type'", content)
        self.assertNotIn("{'content'", content)
        self.assertNotIn("type': 'message'", content)


class TestReasoningOnlyWritesEmpty(TestCase):
    """Test that reasoning-only responses write empty runtime file."""

    def test_reasoning_only_writes_empty_runtime_file(self):
        """When there is no final message, the runtime file should be overwritten with empty string."""
        from src.task_python import _extract_final_message, _save_latest_python_output

        output_dir = Path(__file__).parent.parent / "runtime" / "python"
        output_file = output_dir / "latest_output.py"

        # Simulate reasoning-only response
        raw_output = [
            {"type": "reasoning", "content": "Let me analyze this problem..."},
            {"type": "thinking", "content": "I need to consider edge cases."},
        ]
        content, reason = _extract_final_message(raw_output)

        self.assertIsNone(content)
        self.assertEqual(reason, "no_final_answer")

        # When no final message exists, save empty string to runtime file
        if not content:
            _save_latest_python_output("")

        saved = output_file.read_text(encoding="utf-8")
        self.assertEqual(saved, "",
                         "Runtime file should contain empty string when no final answer")


class TestExistingResponseFormats(TestCase):
    """Test that existing string/dict/list response formats remain supported."""

    def test_string_response(self):
        """Plain string responses must work."""
        from src.task_python import _extract_final_message

        raw = "def hello():\n    print('world')"
        content, reason = _extract_final_message(raw)

        self.assertEqual(content, "def hello():\n    print('world')")
        self.assertEqual(reason, "")

    def test_dict_with_output_key(self):
        """Dict with 'output' key must work."""
        from src.task_python import _extract_final_message

        raw = {"output": "class Counter:\n    pass"}
        content, reason = _extract_final_message(raw)

        self.assertEqual(content, "class Counter:\n    pass")
        self.assertEqual(reason, "")

    def test_dict_with_text_key(self):
        """Dict with 'text' key must work."""
        from src.task_python import _extract_final_message

        raw = {"text": "x = 42"}
        content, reason = _extract_final_message(raw)

        self.assertEqual(content, "x = 42")
        self.assertEqual(reason, "")

    def test_list_of_strings(self):
        """List of plain strings must work (legacy format)."""
        from src.task_python import _extract_final_message

        raw = ["def foo():", "    return 1"]
        content, reason = _extract_final_message(raw)

        # Should extract last message block or fall back gracefully
        # For list of non-dict strings, it falls to no_final_answer
        self.assertIsNone(content)
        self.assertEqual(reason, "no_final_answer")


class TestRuntimeOverwriteEachRun(TestCase):
    """Test that runtime file is overwritten on each run."""

    def test_runtime_file_overwritten_each_run(self):
        """Simulate two consecutive 'runs' — second must completely replace first."""
        from src.task_python import _save_latest_python_output, _extract_final_message

        output_dir = Path(__file__).parent.parent / "runtime" / "python"
        output_file = output_dir / "latest_output.py"

        # Simulate run 1: reasoning + message response
        raw1 = [
            {"type": "reasoning", "content": "Run 1 analysis..."},
            {"type": "message", "content": "# Run 1 solution\ndef solve(): return 1\n"},
        ]
        code1, _ = _extract_final_message(raw1)
        self.assertIsNotNone(code1)
        _save_latest_python_output(code1)

        # Verify run 1 content
        self.assertEqual(output_file.read_text(encoding="utf-8"), code1)

        # Simulate run 2: different reasoning + message response
        raw2 = [
            {"type": "reasoning", "content": "Run 2 analysis..."},
            {"type": "message", "content": "# Run 2 solution\ndef solve(): return 2\n"},
        ]
        code2, _ = _extract_final_message(raw2)
        self.assertIsNotNone(code2)
        _save_latest_python_output(code2)

        # Verify run 2 content completely replaced run 1
        saved = output_file.read_text(encoding="utf-8")
        self.assertEqual(saved, code2)
        self.assertNotIn("Run 1", saved)
        self.assertNotIn("return 1", saved)


if __name__ == "__main__":
    main()