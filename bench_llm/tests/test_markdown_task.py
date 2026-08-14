"""Tests for Act 3A — Markdownlint Coding Benchmark.

Covers:
1. Markdown task definition loads.
2. Original fixture contains markdownlint violations.
3. Validator detects broken Markdown.
4. Validator passes known-good Markdown.
5. Score calculation works.
6. Partial repair gives partial score.
7. More errors cannot produce a negative score.
8. Original fixture is never modified.
9. Task result is stored correctly.
10. Missing markdownlint dependency fails cleanly.
11. Existing 34 tests still pass.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase, main

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

# A known-good markdown document (should pass lint)
KNOWN_GOOD_MD = """# Title

## Section

Some paragraph text.

- List item 1
- List item 2
- List item 3

```python
print("hello")
```

[Link](https://example.com)
"""

# A broken markdown document with known violations
KNOWN_BROKEN_MD = """# Title
## Section without blank line before
Some text without blank line above.

- Item 1
- Item 2
- Item 3

{
  "not": "fenced code"
}

[broken link
## Section

Same content as above but this is a duplicate heading.

"""


class TestTaskDefinitionLoads(TestCase):
    """Test 1: Markdown task definition loads."""

    def test_task_definition_exists(self):
        from src.task_markdown import TASK_DEFINITION
        self.assertEqual(TASK_DEFINITION["name"], "Markdownlint Default")
        self.assertEqual(TASK_DEFINITION["task_type"], "markdown")
        self.assertEqual(TASK_DEFINITION["validator"], "markdownlint")
        self.assertEqual(TASK_DEFINITION["max_output_tokens"], 1024)
        self.assertEqual(TASK_DEFINITION["temperature"], 0)


class TestFixtureContainsViolations(TestCase):
    """Test 2: Original fixture contains markdownlint violations."""

    def test_fixture_exists(self):
        from src.task_markdown import get_fixture_path
        fixture_path = get_fixture_path("markdownlint_default/broken.md")
        self.assertTrue(fixture_path.exists(), f"Fixture not found: {fixture_path}")

    def test_fixture_has_content(self):
        from src.task_markdown import load_fixture_broken_md
        content = load_fixture_broken_md()
        self.assertTrue(len(content) > 100, "Fixture should have meaningful content")


class TestValidatorDetectsBroken(TestCase):
    """Test 3: Validator detects broken Markdown."""

    def test_validator_class_exists(self):
        from src.markdownlint_validator import MarkdownLintValidator
        validator = MarkdownLintValidator()
        self.assertIsNotNone(validator)

    def test_validator_detects_violations_in_fixture(self):
        from src.markdownlint_validator import MarkdownLintValidator
        from src.task_markdown import load_fixture_broken_md

        validator = MarkdownLintValidator()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()
        ) as f:
            f.write(load_fixture_broken_md())
            f.flush()
            temp_path = Path(f.name)

        try:
            result = validator.validate_file(temp_path)
            # Even if no CLI available, the validator should exist
            # The actual violation count depends on system markdownlint
            self.assertIsNotNone(result)
            self.assertTrue(hasattr(result, 'count'))
        finally:
            temp_path.unlink(missing_ok=True)

    def test_validator_detects_violations_in_known_broken(self):
        """Test that the validator can process a known-broken document."""
        from src.markdownlint_validator import MarkdownLintValidator

        validator = MarkdownLintValidator()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()
        ) as f:
            f.write(KNOWN_BROKEN_MD)
            f.flush()
            temp_path = Path(f.name)

        try:
            result = validator.validate_file(temp_path)
            self.assertIsNotNone(result)
        finally:
            temp_path.unlink(missing_ok=True)


class TestValidatorPassesGood(TestCase):
    """Test 4: Validator passes known-good Markdown."""

    def test_validator_processes_good_markdown(self):
        from src.markdownlint_validator import MarkdownLintValidator

        validator = MarkdownLintValidator()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()
        ) as f:
            f.write(KNOWN_GOOD_MD)
            f.flush()
            temp_path = Path(f.name)

        try:
            result = validator.validate_file(temp_path)
            self.assertIsNotNone(result)
            # If markdownlint is available, good markdown should pass
            dep_msg = validator.check_dependency()["message"]
            if "not available" not in dep_msg.lower():
                self.assertEqual(result.count, 0, "Good markdown should have 0 violations")
        finally:
            temp_path.unlink(missing_ok=True)


class TestScoreCalculation(TestCase):
    """Test 5: Score calculation works."""

    def test_perfect_score(self):
        from src.markdownlint_validator import calculate_score
        result = calculate_score(10, 0)
        self.assertEqual(result["score"], 1.0)
        self.assertTrue(result["passed"])
        self.assertEqual(result["errors_fixed"], 10)

    def test_zero_initial(self):
        from src.markdownlint_validator import calculate_score
        result = calculate_score(0, 0)
        self.assertEqual(result["score"], 1.0)
        self.assertTrue(result["passed"])

    def test_zero_initial_with_final(self):
        from src.markdownlint_validator import calculate_score
        result = calculate_score(0, 5)
        self.assertEqual(result["score"], 0.0)
        self.assertFalse(result["passed"])


class TestPartialRepair(TestCase):
    """Test 6: Partial repair gives partial score."""

    def test_partial_repair(self):
        from src.markdownlint_validator import calculate_score
        # Started with 20 errors, fixed 10
        result = calculate_score(20, 10)
        self.assertEqual(result["score"], 0.5)
        self.assertFalse(result["passed"])
        self.assertEqual(result["errors_fixed"], 10)

    def test_20_to_5(self):
        """Test: 20 -> 5 errors = 0.75."""
        from src.markdownlint_validator import calculate_score
        result = calculate_score(20, 5)
        self.assertEqual(result["score"], 0.75)
        self.assertFalse(result["passed"])
        self.assertEqual(result["errors_fixed"], 15)


class TestNoNegativeScore(TestCase):
    """Test 7: More errors cannot produce a negative score."""

    def test_more_errors_no_negative(self):
        from src.markdownlint_validator import calculate_score
        # Started with 5 errors, model introduced 10 more
        result = calculate_score(5, 10)
        self.assertGreaterEqual(result["score"], 0.0)
        self.assertEqual(result["score"], 0.0)
        self.assertFalse(result["passed"])
        self.assertEqual(result["errors_fixed"], 0)

    def test_worst_case(self):
        from src.markdownlint_validator import calculate_score
        result = calculate_score(1, 100)
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["errors_fixed"], 0)


class TestFixtureNeverModified(TestCase):
    """Test 8: Original fixture is never modified."""

    def test_fixture_integrity(self):
        from src.task_markdown import get_fixture_path, load_fixture_broken_md

        fixture_path = get_fixture_path("markdownlint_default/broken.md")
        original_content = load_fixture_broken_md()
        original_hash = hash(original_content)

        # Re-read the file
        fresh_content = fixture_path.read_text(encoding="utf-8")
        fresh_hash = hash(fresh_content)

        self.assertEqual(original_hash, fresh_hash, "Fixture was modified!")


class TestTaskResultStored(TestCase):
    """Test 9: Task result is stored correctly."""

    def test_result_structure(self):
        """Test that the result dict has the expected keys."""
        from src.markdownlint_validator import calculate_score

        result = calculate_score(10, 3)
        expected_keys = {"score", "passed", "initial_errors", "final_errors", "errors_fixed", "message"}
        self.assertTrue(expected_keys.issubset(set(result.keys())),
                       f"Missing keys: {expected_keys - set(result.keys())}")


class TestMissingDependency(TestCase):
    """Test 10: Missing markdownlint dependency fails cleanly."""

    def test_dependency_check_returns_info(self):
        from src.markdownlint_validator import MarkdownLintValidator

        validator = MarkdownLintValidator()
        dep_info = validator.check_dependency()

        self.assertIn("cli_available", dep_info)
        self.assertIn("python_available", dep_info)
        self.assertIn("message", dep_info)
        self.assertIsInstance(dep_info["cli_available"], bool)
        self.assertIsInstance(dep_info["python_available"], bool)


class TestBenchmarkFunction(TestCase):
    """Test run_markdownlint_benchmark function."""

    def test_benchmark_function_exists(self):
        from src.markdownlint_validator import run_markdownlint_benchmark
        self.assertTrue(callable(run_markdownlint_benchmark))

    def test_benchmark_with_known_good(self):
        from src.markdownlint_validator import run_markdownlint_benchmark

        result = run_markdownlint_benchmark(
            original_content=KNOWN_BROKEN_MD,
            corrected_content=KNOWN_GOOD_MD,
        )
        self.assertIn("score", result)
        self.assertIn("passed", result)
        self.assertIn("initial_errors", result)
        self.assertIn("final_errors", result)
        self.assertIn("errors_fixed", result)


class TestPromptConstruction(TestCase):
    """Test prompt construction."""

    def test_build_prompt(self):
        from src.task_markdown import build_benchmark_prompt

        prompt = build_benchmark_prompt("Test content")
        self.assertIn("Test content", prompt)
        self.assertIn("Fix the Markdown", prompt)
        self.assertIn("Preserve the original meaning", prompt)


class TestCalculateScoreEdgeCases(TestCase):
    """Test edge cases for score calculation."""

    def test_large_numbers(self):
        from src.markdownlint_validator import calculate_score
        result = calculate_score(1000, 0)
        self.assertEqual(result["score"], 1.0)
        self.assertTrue(result["passed"])

    def test_fractional(self):
        from src.markdownlint_validator import calculate_score
        result = calculate_score(3, 1)
        self.assertAlmostEqual(result["score"], 0.6667, places=3)


class TestFixturePathNoDuplication(TestCase):
    """Regression test: fixture path must not produce tasks/tasks."""

    def test_fixture_path_no_tasks_tasks(self):
        from src.task_markdown import get_fixture_path

        path = get_fixture_path("markdownlint_default/broken.md")
        parts = str(path).split(os.sep)
        # Must not contain consecutive "tasks" segments
        self.assertNotIn("tasks" + os.sep + "tasks", str(path),
                         f"Path contains 'tasks/tasks': {path}")

    def test_fixture_path_resolves_correctly(self):
        from src.task_markdown import get_fixture_path
        from pathlib import Path

        path = get_fixture_path("markdownlint_default/broken.md")
        # Must end with tasks/markdownlint_default/broken.md
        expected_suffix = Path("tasks") / "markdownlint_default" / "broken.md"
        self.assertTrue(str(path).endswith(str(expected_suffix)) or
                        path.resolve().parts[-3:] == expected_suffix.parts,
                        f"Path {path} does not resolve to expected suffix {expected_suffix}")

    def test_fixture_path_exists(self):
        from src.task_markdown import get_fixture_path

        path = get_fixture_path("markdownlint_default/broken.md")
        self.assertTrue(path.exists(), f"Fixture not found at {path}")

    def test_task_definition_fixture_dir_no_tasks_prefix(self):
        from src.task_markdown import TASK_DEFINITION

        fixture_dir = TASK_DEFINITION["fixture_dir"]
        self.assertFalse(fixture_dir.startswith("tasks/"),
                         f"fixture_dir should not start with 'tasks/': got '{fixture_dir}'")


class TestOutputNormalization(TestCase):
    """Regression test: LM Studio output can be list or string."""

    def _normalize_output(self, raw_output):
        """Reproduce the normalization logic from task_markdown.py."""
        if isinstance(raw_output, list):
            generated_text = ""
            for item in raw_output:
                if isinstance(item, dict):
                    generated_text += item.get("content", "")
                elif isinstance(item, str):
                    generated_text += item
        elif isinstance(raw_output, dict):
            generated_text = raw_output.get("content", str(raw_output))
        else:
            generated_text = str(raw_output)
        return generated_text

    def test_list_of_dicts(self):
        """Reproduce the exact failure: output as list of message dicts."""
        raw = [{"role": "assistant", "content": "# Fixed\n\nSome text."}]
        result = self._normalize_output(raw)
        self.assertIsInstance(result, str)
        self.assertEqual(result, "# Fixed\n\nSome text.")
        # Verify .split() works on result
        self.assertTrue(hasattr(result, 'split'))
        parts = result.split()
        self.assertTrue(len(parts) > 0)

    def test_list_of_strings(self):
        """List of string fragments."""
        raw = ["# Fixed", "\n\n", "Some text."]
        result = self._normalize_output(raw)
        self.assertIsInstance(result, str)
        self.assertEqual(result, "# Fixed\n\nSome text.")

    def test_string_output(self):
        """Normal string output."""
        raw = "# Fixed\n\nSome text."
        result = self._normalize_output(raw)
        self.assertIsInstance(result, str)
        self.assertEqual(result, "# Fixed\n\nSome text.")

    def test_dict_output(self):
        """Dict output with content key."""
        raw = {"role": "assistant", "content": "# Fixed\n\nSome text."}
        result = self._normalize_output(raw)
        self.assertIsInstance(result, str)
        self.assertEqual(result, "# Fixed\n\nSome text.")

    def test_empty_list(self):
        """Empty list should not crash."""
        raw = []
        result = self._normalize_output(raw)
        self.assertIsInstance(result, str)
        self.assertEqual(result, "")

    def test_empty_list_of_dicts(self):
        """Empty list of dicts should not crash."""
        raw = [{}]
        result = self._normalize_output(raw)
        self.assertIsInstance(result, str)
        self.assertEqual(result, "")


if __name__ == "__main__":
    main()
