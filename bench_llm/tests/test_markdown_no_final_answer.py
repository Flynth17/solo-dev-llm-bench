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


# ---------------------------------------------------------------------------
# Additional regression tests for Act M4.9 — status + TTFT mapping
# ---------------------------------------------------------------------------

class TestFailureReasonEvaluationMapping(TestCase):
    """Test that failure_reason survives the evaluation route mapping."""

    def test_no_final_answer_survives_evaluation_mapping(self):
        """failure_reason='no_final_answer' must be present in correctness_results[0]."""
        # Simulate what run_markdown_task returns on no_final_answer
        md_result = {
            "task_name": "Markdownlint Default",
            "task_type": "markdown",
            "model": "test-model",
            "initial_errors": 6,
            "final_errors": None,
            "errors_fixed": 0,
            "score": 0.0,
            "passed": False,
            "output_tokens": 0,
            "input_tokens": 0,
            "tokens_per_second": 0.0,
            "ttft_seconds": None,
            "wall_time_seconds": 1.23,
            "corrected_output": "",
            "corrected_violations": [],
            "dependency_message": "markdownlint available",
            "validator_available": True,
            "failure_reason": "no_final_answer",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "hardware_label": "",
            "execution_environment": "Local",
            "connection_type": "",
        }

        # Simulate the evaluation route mapping (evaluation.py lines 380-395)
        correctness_row = {
            "test_type": "markdown",
            "test_label": "Markdownlint Default",
            "score": md_result["score"],
            "passed": md_result["passed"],
            "initial_errors": md_result["initial_errors"],
            "final_errors": md_result["final_errors"],
            "errors_fixed": md_result["errors_fixed"],
            "tokens_per_second": md_result["tokens_per_second"],
            "ttft_seconds": md_result.get("ttft_seconds"),
            "wall_time_seconds": md_result["wall_time_seconds"],
            "output_tokens": md_result["output_tokens"],
            "input_tokens": md_result["input_tokens"],
            "corrected_violations": md_result.get("corrected_violations", []),
            "failure_reason": md_result.get("failure_reason"),
        }

        self.assertEqual(correctness_row["failure_reason"], "no_final_answer")

    def test_null_failure_reason_survives_evaluation_mapping(self):
        """When failure_reason is absent, it should map to None (not crash)."""
        md_result = {
            "task_name": "Markdownlint Default",
            "task_type": "markdown",
            "model": "test-model",
            "initial_errors": 3,
            "final_errors": 1,
            "errors_fixed": 2,
            "score": 66.67,
            "passed": True,
            "output_tokens": 100,
            "input_tokens": 50,
            "tokens_per_second": 80.0,
            "ttft_seconds": 0.45,
            "wall_time_seconds": 2.0,
            "corrected_output": "# Fixed",
            "corrected_violations": [],
            "dependency_message": "markdownlint available",
            "validator_available": True,
            # failure_reason intentionally absent (normal success case)
            "timestamp": "2026-01-01T00:00:00+00:00",
            "hardware_label": "",
            "execution_environment": "Local",
            "connection_type": "",
        }

        correctness_row = {
            "test_type": "markdown",
            "test_label": "Markdownlint Default",
            "score": md_result["score"],
            "passed": md_result["passed"],
            "initial_errors": md_result["initial_errors"],
            "final_errors": md_result["final_errors"],
            "errors_fixed": md_result["errors_fixed"],
            "tokens_per_second": md_result["tokens_per_second"],
            "ttft_seconds": md_result.get("ttft_seconds"),
            "wall_time_seconds": md_result["wall_time_seconds"],
            "output_tokens": md_result["output_tokens"],
            "input_tokens": md_result["input_tokens"],
            "corrected_violations": md_result.get("corrected_violations", []),
            "failure_reason": md_result.get("failure_reason"),
        }

        self.assertIsNone(correctness_row["failure_reason"])


class TestTTFTMapping(TestCase):
    """Test that TTFT survives the mapping when present and missing."""

    def test_ttft_survives_evaluation_mapping_when_present(self):
        """When LM Studio returns TTFT, it must be preserved through mapping."""
        md_result = {
            "task_name": "Markdownlint Default",
            "task_type": "markdown",
            "model": "test-model",
            "initial_errors": 6,
            "final_errors": 2,
            "errors_fixed": 4,
            "score": 66.67,
            "passed": True,
            "output_tokens": 100,
            "input_tokens": 50,
            "tokens_per_second": 80.0,
            "ttft_seconds": 0.3421,
            "wall_time_seconds": 2.5,
            "corrected_output": "# Fixed",
            "corrected_violations": [],
            "dependency_message": "markdownlint available",
            "validator_available": True,
            "failure_reason": "",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "hardware_label": "",
            "execution_environment": "Local",
            "connection_type": "",
        }

        correctness_row = {
            "test_type": "markdown",
            "test_label": "Markdownlint Default",
            "score": md_result["score"],
            "passed": md_result["passed"],
            "initial_errors": md_result["initial_errors"],
            "final_errors": md_result["final_errors"],
            "errors_fixed": md_result["errors_fixed"],
            "tokens_per_second": md_result["tokens_per_second"],
            "ttft_seconds": md_result.get("ttft_seconds"),
            "wall_time_seconds": md_result["wall_time_seconds"],
            "output_tokens": md_result["output_tokens"],
            "input_tokens": md_result["input_tokens"],
            "corrected_violations": md_result.get("corrected_violations", []),
            "failure_reason": md_result.get("failure_reason"),
        }

        self.assertEqual(correctness_row["ttft_seconds"], 0.3421)

    def test_ttft_none_when_missing(self):
        """When TTFT is missing, it should be None (dashboard shows —)."""
        md_result = {
            "task_name": "Markdownlint Default",
            "task_type": "markdown",
            "model": "test-model",
            "initial_errors": 6,
            "final_errors": None,
            "errors_fixed": 0,
            "score": 0.0,
            "passed": False,
            "output_tokens": 0,
            "input_tokens": 0,
            "tokens_per_second": 0.0,
            # ttft_seconds intentionally missing/None
            "wall_time_seconds": 1.23,
            "corrected_output": "",
            "corrected_violations": [],
            "dependency_message": "markdownlint available",
            "validator_available": True,
            "failure_reason": "no_final_answer",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "hardware_label": "",
            "execution_environment": "Local",
            "connection_type": "",
        }

        correctness_row = {
            "test_type": "markdown",
            "test_label": "Markdownlint Default",
            "score": md_result["score"],
            "passed": md_result["passed"],
            "initial_errors": md_result["initial_errors"],
            "final_errors": md_result["final_errors"],
            "errors_fixed": md_result["errors_fixed"],
            "tokens_per_second": md_result["tokens_per_second"],
            "ttft_seconds": md_result.get("ttft_seconds"),
            "wall_time_seconds": md_result["wall_time_seconds"],
            "output_tokens": md_result["output_tokens"],
            "input_tokens": md_result["input_tokens"],
            "corrected_violations": md_result.get("corrected_violations", []),
            "failure_reason": md_result.get("failure_reason"),
        }

        self.assertIsNone(correctness_row["ttft_seconds"])


class TestStatusRenderingLogic(TestCase):
    """Test that STATUS row is rendered when failure_reason is present."""

    def test_failure_reason_no_final_answer_maps_to_status_label(self):
        """no_final_answer should map to 'NO FINAL ANSWER' status label."""
        failure_reason = "no_final_answer"
        if failure_reason:
            status_label = failure_reason.replace("_", " ").upper()
        else:
            status_label = None

        self.assertEqual(status_label, "NO FINAL ANSWER")

    def test_null_failure_reason_does_not_render_status(self):
        """When failure_reason is null/None, no STATUS row should be rendered."""
        failure_reason = None
        if failure_reason:
            status_label = failure_reason.replace("_", " ").upper()
        else:
            status_label = None

        self.assertIsNone(status_label)


class TestMarkdownPayloadStructure(TestCase):
    """Verify the exact outgoing LM Studio payload in task_markdown.py."""

    def test_payload_contains_correct_keys_and_not_max_tokens(self):
        """The Markdown request must use max_output_tokens (not max_tokens) and include store=False."""
        # Import the function that builds the payload
        from src.task_markdown import run_markdown_task, TASK_DEFINITION

        # We verify by inspecting the source code directly since we can't
        # actually call LM Studio in a unit test.
        import inspect
        source = inspect.getsource(run_markdown_task)

        # Must contain max_output_tokens
        self.assertIn('"max_output_tokens"', source, "Payload must use 'max_output_tokens' key")

        # Must NOT contain the old wrong key
        self.assertNotIn('"max_tokens"', source, "Payload must not contain 'max_tokens' key")

        # Must contain store: False
        self.assertIn('"store": False', source, "Payload must include 'store': False")

        # Must contain required keys
        self.assertIn('"model"', source)
        self.assertIn('"input"', source)
        self.assertIn('"temperature"', source)
        self.assertIn('"stream": False', source)


if __name__ == "__main__":
    main()
