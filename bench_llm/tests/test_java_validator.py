"""Tests for the Java correctness validator.

Tests cover:
1. Broken fixture fails (partial score < 1.0)
2. Correct fixture scores 1.0
3. Partial solution gets partial score
4. Compile error scores 0
5. Runtime error handled
6. Timeout handled
7. Missing javac produces clear validator error
8. Test harness remains hidden from model prompt
9. Deterministic test count derivation
10. Result dataclass has all required fields
"""

import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from src.java_validator import (
    validate_java_solution,
    JavaValidationResult,
    _count_expected_tests,
    _parse_results,
    _TEST_SOLUTION,
    _javac_available,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def _load_fixture(filename):
    """Load a fixture file from the java_correctness fixture directory."""
    fixture_dir = os.path.join(os.path.dirname(__file__), "..", "tasks", "java_correctness")
    path = os.path.join(fixture_dir, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _correct_code():
    """Return the correct Solution.java code."""
    return '''/**
 * A simple calculator utility class.
 */
public class Solution {

    /**
     * Add two integers and return the result.
     */
    public static int add(int a, int b) {
        return a + b;
    }

    /**
     * Multiply two integers and return the result.
     */
    public static int multiply(int x, int y) {
        return x * y;
    }

    /**
     * Return true if n is even, false otherwise.
     */
    public static boolean isEven(int n) {
        return n % 2 == 0;
    }

    /**
     * Return the absolute value of n.
     */
    public static int absolute(int n) {
        if (n < 0) {
            return -n;
        }
        return n;
    }

    /**
     * Return the greeting for a given name.
     */
    public static String greeting(String name) {
        return "Hello " + name + "!";
    }
}
'''


# ------------------------------------------------------------------
# Test 1: Broken fixture fails (partial score)
# ------------------------------------------------------------------

class TestBrokenFixture:
    """Test that the broken fixture produces partial score (not 0, not 1)."""

    def test_broken_fixture_fails(self):
        """Broken solution.py must NOT score 1.0."""
        broken_code = _load_fixture("Solution.java")
        result = validate_java_solution(broken_code)

        assert result.score < 1.0
        assert result.passed is False
        assert result.compile_success is True
        assert result.passed_tests < result.total_tests


# ------------------------------------------------------------------
# Test 2: Correct fixture scores 1.0
# ------------------------------------------------------------------

class TestCorrectFixture:
    """Test that a correct solution scores 1.0."""

    def test_correct_fixture_scores_1(self):
        """Correct solution must produce score 1.0."""
        result = validate_java_solution(_correct_code())

        assert result.score == 1.0
        assert result.passed is True
        assert result.passed_tests == result.total_tests
        assert result.failed_tests == 0
        assert result.compile_success is True


# ------------------------------------------------------------------
# Test 3: Partial solution gets partial score
# ------------------------------------------------------------------

class TestPartialSolution:
    """Test that a partial solution gets a partial score."""

    def test_partial_solution_score(self):
        """Partial solution (only add fixed) must produce 0 < score < 1."""
        partial_code = '''/**
 * A simple calculator utility class.
 */
public class Solution {

    public static int add(int a, int b) {
        return a + b;
    }

    public static int multiply(int x, int y) {
        return x + y;
    }

    public static boolean isEven(int n) {
        return n % 2 == 1;
    }

    public static int absolute(int n) {
        if (n > 0) {
            return -n;
        }
        return n;
    }

    public static String greeting(String name) {
        return "Hello" + name + "!";
    }
}
'''
        result = validate_java_solution(partial_code)

        assert 0.0 < result.score < 1.0
        assert result.passed is False
        assert result.failed_tests > 0


# ------------------------------------------------------------------
# Test 4: Compile error scores 0
# ------------------------------------------------------------------

class TestCompileError:
    """Test that syntax-broken code produces score 0."""

    def test_syntax_error_scores_0(self):
        """Code with syntax errors must produce score 0.0."""
        broken_code = "public class Solution { public int add(int a, int b) { return a + b; }"
        result = validate_java_solution(broken_code)

        assert result.score == 0.0
        assert result.passed is False
        assert result.compile_success is False
        assert result.passed_tests == 0
        assert result.failed_tests == result.total_tests


# ------------------------------------------------------------------
# Test 5: Runtime error handled
# ------------------------------------------------------------------

class TestRuntimeError:
    """Test that runtime errors (e.g., missing method) are handled."""

    def test_missing_class_handled(self):
        """Code that doesn't compile as Solution class is handled."""
        wrong_code = "public class Other { public static int add(int a, int b) { return a + b; } }"
        result = validate_java_solution(wrong_code)

        # This will fail compilation because class is named Other, not Solution
        assert result.score == 0.0
        assert result.passed is False


# ------------------------------------------------------------------
# Test 6: Timeout handled
# ------------------------------------------------------------------

class TestTimeout:
    """Test that timeout produces deterministic failure."""

    def test_timeout_returns_zero_score(self):
        """TimeoutExpired must produce score 0.0 and timed_out=True."""
        # We test the exception handling path by patching subprocess.run
        # to raise TimeoutExpired during the java run step (after javac check).
        from unittest.mock import patch, MagicMock
        import subprocess

        # First call = javac -version (should succeed)
        # Second call = javac Solution.java TestSolution.java (should succeed)
        # Third call = java TestSolution (should timeout)
        javac_version_result = MagicMock()
        javac_version_result.returncode = 0
        javac_version_result.stdout = b""
        javac_version_result.stderr = b""

        javac_compile_result = MagicMock()
        javac_compile_result.returncode = 0
        javac_compile_result.stdout = b""
        javac_compile_result.stderr = b""

        call_count = [0]

        def mock_run(*args, **kwargs):
            call_count[0] += 1
            cmd = args[0] if args else kwargs.get("args", [])
            if "javac" in cmd and "-version" in cmd:
                return javac_version_result
            if "javac" in cmd:
                return javac_compile_result
            # java TestSolution -> timeout
            raise subprocess.TimeoutExpired(args[0], 10)

        with patch("src.java_validator.subprocess.run", side_effect=mock_run):
            result = validate_java_solution(_correct_code())

        assert result.score == 0.0
        assert result.passed is False
        assert result.timed_out is True


# ------------------------------------------------------------------
# Test 7: Missing javac produces clear validator error
# ------------------------------------------------------------------

class TestMissingJavac:
    """Test behavior when javac is not available."""

    def test_javac_available_is_true(self):
        """On this system, javac should be available."""
        assert _javac_available() is True

    def test_javac_not_available_produces_error(self):
        """When javac is missing, must produce clear error message."""
        from unittest.mock import patch

        with patch("src.java_validator._javac_available", return_value=False):
            result = validate_java_solution(_correct_code())

        assert result.score == 0.0
        assert result.passed is False
        assert result.compile_success is False
        assert "javac not found" in result.error


# ------------------------------------------------------------------
# Test 8: Test harness remains hidden from model prompt
# ------------------------------------------------------------------

class TestHarnessHidden:
    """Test that TestSolution.java content is not in the model prompt."""

    def test_test_fixture_not_in_prompt(self):
        """TestSolution.java must NOT be included in the model prompt."""
        # The prompt.md should only contain instructions, not test code
        prompt_dir = os.path.join(
            os.path.dirname(__file__), "..", "tasks", "java_correctness"
        )
        prompt_path = Path(os.path.join(prompt_dir, "prompt.md"))
        assert prompt_path.exists()

        prompt_content = prompt_path.read_text(encoding="utf-8")

        # Must NOT contain test code patterns
        assert "TestSolution" not in prompt_content
        assert "check(" not in prompt_content
        assert "testAdd" not in prompt_content

    def test_test_fixture_exists(self):
        """TestSolution.java must exist as a file."""
        test_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "tasks",
            "java_correctness",
            "TestSolution.java",
        )
        assert os.path.exists(test_path)


# ------------------------------------------------------------------
# Test 9: Deterministic test count derivation
# ------------------------------------------------------------------

class TestTestCountDerivation:
    """Test that expected test count is derived from TestSolution.java."""

    def test_expected_test_count(self):
        """Must derive correct test count from fixture."""
        count = _count_expected_tests()
        assert count > 0
        assert count == 7  # We have 7 check() calls in TestSolution.java

    def test_count_matches_fixture(self):
        """Derived count must match actual test count in fixture."""
        import re
        pattern = r'check\("([^"]+)"'
        actual_count = len(re.findall(pattern, _TEST_SOLUTION))
        derived_count = _count_expected_tests()
        assert derived_count == actual_count


# ------------------------------------------------------------------
# Test 10: Result dataclass has all required fields
# ------------------------------------------------------------------

class TestResultDataclass:
    """Test that JavaValidationResult has all required fields."""

    def test_all_fields_present(self):
        """Must have all required fields."""
        result = JavaValidationResult(
            total_tests=5,
            passed_tests=3,
            failed_tests=2,
            score=0.6,
            passed=False,
            compile_success=True,
            exit_code=0,
            timed_out=False,
            stdout="PASS test1\nPASS test2\nFAIL test3",
            stderr="",
        )

        assert hasattr(result, "total_tests")
        assert hasattr(result, "passed_tests")
        assert hasattr(result, "failed_tests")
        assert hasattr(result, "score")
        assert hasattr(result, "passed")
        assert hasattr(result, "compile_success")
        assert hasattr(result, "exit_code")
        assert hasattr(result, "timed_out")
        assert hasattr(result, "stdout")
        assert hasattr(result, "stderr")
        assert hasattr(result, "error")

    def test_default_error_is_empty(self):
        """Default error field must be empty string."""
        result = JavaValidationResult(
            total_tests=5,
            passed_tests=3,
            failed_tests=2,
            score=0.6,
            passed=False,
            compile_success=True,
            exit_code=0,
            timed_out=False,
            stdout="",
            stderr="",
        )
        assert result.error == ""


# ------------------------------------------------------------------
# Test 11: _parse_results helper
# ------------------------------------------------------------------

class TestParseResults:
    """Test the _parse_results helper function."""

    def test_parse_all_pass(self):
        """All PASS markers must return correct counts."""
        stdout = "PASS test1\nPASS test2\nPASS test3\n"
        passed, failed = _parse_results(stdout)
        assert passed == 3
        assert failed == 0

    def test_parse_all_fail(self):
        """All FAIL markers must return correct counts."""
        stdout = "FAIL test1\nFAIL test2\n"
        passed, failed = _parse_results(stdout)
        assert passed == 0
        assert failed == 2

    def test_parse_mixed(self):
        """Mixed PASS/FAIL must return correct counts."""
        stdout = "PASS test1\nFAIL test2\nPASS test3\nFAIL test4\nFAIL test5\n"
        passed, failed = _parse_results(stdout)
        assert passed == 2
        assert failed == 3

    def test_parse_empty(self):
        """Empty input must return 0/0."""
        passed, failed = _parse_results("")
        assert passed == 0
        assert failed == 0

    def test_parse_summary_ignored(self):
        """SUMMARY line must be ignored (not counted as PASS/FAIL)."""
        stdout = "PASS test1\nPASS test2\nSUMMARY 2/2\n"
        passed, failed = _parse_results(stdout)
        assert passed == 2
        assert failed == 0


# ------------------------------------------------------------------
# Test 12: Prompt fixture exists and loads
# ------------------------------------------------------------------

class TestPromptFixture:
    """Test that the Java prompt fixture exists and loads correctly."""

    def test_prompt_fixture_exists(self):
        """prompt.md must exist."""
        prompt_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "tasks",
            "java_correctness",
            "prompt.md",
        )
        assert os.path.exists(prompt_path)

    def test_prompt_fixture_loads(self):
        """prompt.md must load and contain instructions."""
        prompt_path = Path(os.path.join(
            os.path.dirname(__file__),
            "..",
            "tasks",
            "java_correctness",
            "prompt.md",
        ))
        content = prompt_path.read_text(encoding="utf-8")
        assert len(content) > 0
        assert "Fix" in content or "fix" in content
        assert "Solution.java" in content


# ------------------------------------------------------------------
# Test 13: Repeated prompt construction is deterministic
# ------------------------------------------------------------------

class TestDeterminism:
    """Test that validation is deterministic across runs."""

    def test_same_code_same_result(self):
        """Running validation twice on the same code must produce same score."""
        code = _correct_code()
        result1 = validate_java_solution(code)
        result2 = validate_java_solution(code)

        assert result1.score == result2.score
        assert result1.passed == result2.passed
        assert result1.passed_tests == result2.passed_tests
        assert result1.total_tests == result2.total_tests

    def test_broken_code_deterministic(self):
        """Broken code must consistently fail."""
        broken = _load_fixture("Solution.java")
        result1 = validate_java_solution(broken)
        result2 = validate_java_solution(broken)

        assert result1.score == result2.score
        assert result1.passed == result2.passed