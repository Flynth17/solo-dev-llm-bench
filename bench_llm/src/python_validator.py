"""Python correctness validator for Solo Dev LLM Bench.

Validates a corrected Python solution by running pytest in an isolated
temporary workspace with a hard timeout.
"""

import importlib
import re
import subprocess
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PythonValidationResult:
    """Result of validating a Python solution."""
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    score: float = 0.0
    passed: bool = False
    exit_code: int = -1
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "score": self.score,
            "passed": self.passed,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
        }


# Default timeout in seconds
DEFAULT_TIMEOUT = 60.0


def validate_python_solution(
    solution_code: str,
    test_code: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> PythonValidationResult:
    """Validate Python solution by running pytest in an isolated temp directory.

    Args:
        solution_code: The corrected Python source code to validate.
        test_code: The pytest test file content.
        timeout: Hard timeout in seconds for the pytest subprocess.

    Returns:
        PythonValidationResult with deterministic validation data.
    """
    result = PythonValidationResult()

    try:
        with tempfile.TemporaryDirectory(dir=tempfile.gettempdir()) as tmpdir:
            workspace = Path(tmpdir)

            # Write solution file
            solution_file = workspace / "solution.py"
            solution_file.write_text(solution_code, encoding="utf-8")

            # Write test file
            test_file = workspace / "test_solution.py"
            test_file.write_text(test_code, encoding="utf-8")

            # Run pytest via subprocess (no shell)
            # Note: removed "-x" so all tests run even if some fail
            # This gives accurate partial scoring for partial solutions
            proc = subprocess.run(
                ["python", "-m", "pytest", "--assert=plain", "--tb=short", "-q", str(test_file)],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            result.exit_code = proc.returncode
            result.stdout = proc.stdout
            result.stderr = proc.stderr

            # Parse pytest output for test counts — robust multi-format parser.
            # pytest -q output format examples:
            #   "6 passed in 0.02s"
            #   "5 passed, 1 failed in 0.03s"
            #   "1 failed, 5 passed in 0.03s"
            #   "...... [100%]"  (dot output lines before summary)
            result.failed_tests = _parse_pytest_count(proc.stdout, "failed")
            result.passed_tests = _parse_pytest_count(proc.stdout, "passed")

            result.total_tests = result.passed_tests + result.failed_tests

            # If total is 0, try to count from output markers
            if result.total_tests == 0:
                # Count "::" occurrences in output which indicate test names
                if "::" in result.stdout:
                    result.total_tests = result.stdout.count("::")
                    result.passed_tests = result.stdout.count("[ PASSED ]")
                    result.failed_tests = result.stdout.count("[ FAILED ]")
                    if result.total_tests == 0:
                        result.total_tests = result.passed_tests + result.failed_tests

            # If still total is 0, the test module couldn't be imported/collection failed.
            # Count expected tests from the test_code by looking for def test_ patterns.
            if result.total_tests == 0:
                result.total_tests = _count_test_functions(test_code)
                result.passed_tests = 0
                result.failed_tests = result.total_tests
                result.error = (
                    "Test module could not be imported or collected. "
                    "This is likely due to a syntax error, missing imports, "
                    "or missing required functions in the solution."
                )

            # Scoring
            if result.total_tests > 0:
                result.score = round(result.passed_tests / result.total_tests, 4)
            else:
                result.score = 0.0

            # Passed = all tests pass
            result.passed = result.failed_tests == 0 and result.total_tests > 0

    except subprocess.TimeoutExpired:
        result.timed_out = True
        result.error = "pytest subprocess timed out"
        result.exit_code = -1
        result.total_tests = _count_test_functions(test_code)
        result.passed_tests = 0
        result.failed_tests = result.total_tests
        result.score = 0.0
        result.passed = False
    except FileNotFoundError:
        result.error = "python executable not found"
        result.exit_code = -1
        result.score = 0.0
        result.passed = False
    except Exception as e:
        result.error = f"unexpected error: {e}"
        result.exit_code = -1
        result.score = 0.0
        result.passed = False
        traceback.print_exc()

    return result


def _parse_pytest_count(stdout: str, keyword: str) -> int:
    """Parse a count for *keyword* from pytest summary output.

    Handles these formats robustly (pytest -q):
      "6 passed in 0.02s"
      "5 passed, 1 failed in 0.03s"
      "1 failed, 5 passed in 0.03s"
      "...... [100%]"   (dot lines — no summary line)

    Returns 0 when the keyword is not found or parsing fails.
    """
    # Strategy 1: parse the LAST non-empty line that contains the keyword.
    # This handles cases where pytest prints dot-lines before the summary.
    lines = [l.strip() for l in stdout.splitlines() if l.strip()]
    for line in reversed(lines):
        if keyword not in line:
            continue
        # Split on the keyword; the token immediately to the left is the count.
        parts = line.split(keyword)
        # parts[0] is everything before "keyword".  Grab the last whitespace-delimited token.
        pre_tokens = parts[0].strip().split()
        if pre_tokens:
            try:
                return int(pre_tokens[-1])
            except ValueError:
                pass
        # If nothing before keyword, count might be at start of line (e.g. "6 failed")
        all_tokens = line.split()
        for i, tok in enumerate(all_tokens):
            if tok == keyword and i + 1 < len(all_tokens):
                try:
                    return int(all_tokens[i + 1])
                except ValueError:
                    pass
                break

    # Strategy 2: regex fallback — look for "<digits> <keyword>" anywhere.
    import re as _re
    m = _re.search(r"(\d+)\s+" + keyword, stdout)
    if m:
        return int(m.group(1))

    return 0


def _count_test_functions(test_code: str) -> int:
    """Count the number of test functions in a pytest test file.

    Looks for patterns like ``def test_`` at the start of a line or
    after indentation.  This provides a deterministic expected test
    count even when the test module cannot be collected.

    Args:
        test_code: The pytest test file content.

    Returns:
        The number of test functions found.
    """
    count = 0
    for line in test_code.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("def test_") and "(" in stripped:
            count += 1
    return count

