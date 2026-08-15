"""Python correctness validator for Solo Dev LLM Bench.

Validates a corrected Python solution by running pytest in an isolated
temporary workspace with a hard timeout.
"""

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
            proc = subprocess.run(
                ["python", "-m", "pytest", "-x", "--tb=short", "-q", str(test_file)],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            result.exit_code = proc.returncode
            result.stdout = proc.stdout
            result.stderr = proc.stderr

            # Parse pytest output for test counts
            # pytest -q output format: "1 failed, 2 passed, 3 skipped in 0.12s"
            output_lines = proc.stdout.strip().split("\n")
            last_line = output_lines[-1] if output_lines else ""

            # Parse counts from last line
            if "failed" in last_line:
                try:
                    # Extract number before "failed"
                    parts = last_line.split("failed")
                    failed_str = parts[0].strip().split()[-1] if parts[0].strip().split() else "0"
                    result.failed_tests = int(failed_str)
                except (ValueError, IndexError):
                    result.failed_tests = 0
            else:
                result.failed_tests = 0

            if "passed" in last_line:
                try:
                    parts = last_line.split("passed")
                    passed_str = parts[0].strip().split()[-1] if parts[0].strip().split() else "0"
                    result.passed_tests = int(passed_str)
                except (ValueError, IndexError):
                    result.passed_tests = 0
            else:
                result.passed_tests = 0

            result.total_tests = result.passed_tests + result.failed_tests

            # If total is 0, try to count from output
            if result.total_tests == 0:
                # Count "::" occurrences in output which indicate test names
                if "::" in result.stdout:
                    result.total_tests = result.stdout.count("::")
                    result.passed_tests = result.stdout.count("[ PASSED ]")
                    result.failed_tests = result.stdout.count("[ FAILED ]")
                    if result.total_tests == 0:
                        result.total_tests = result.passed_tests + result.failed_tests

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