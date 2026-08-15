"""Deterministic Java correctness validator for Solo Dev LLM Bench.

Uses subprocess with javac + java (no JUnit, no Maven/Gradle).
Each test prints PASS/FAIL testName markers and a SUMMARY line.
"""

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ------------------------------------------------------------------
# Result dataclass
# ------------------------------------------------------------------

@dataclass
class JavaValidationResult:
    total_tests: int
    passed_tests: int
    failed_tests: int
    score: float
    passed: bool
    compile_success: bool
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    error: str = ""


# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

COMPILE_TIMEOUT = 30  # seconds
RUN_TIMEOUT = 15      # seconds
EXPECTED_TEST_COUNT = 7  # derived from TestSolution.java


# ------------------------------------------------------------------
# TestSolution.java fixture (embedded)
# ------------------------------------------------------------------

_TEST_SOLUTION = """
/**
 * Deterministic test harness for Solution.java.
 * Does NOT use JUnit -- compiles with plain javac.
 * Each test prints PASS/FAIL testName.
 * Prints SUMMARY passed/total at the end.
 */
public class TestSolution {

    private static int passed = 0;
    private static int failed = 0;

    private static void check(String testName, boolean condition) {
        if (condition) {
            System.out.println("PASS " + testName);
            passed++;
        } else {
            System.out.println("FAIL " + testName);
            failed++;
        }
    }

    public static void main(String[] args) {
        // Test 1: add(2, 3) == 5
        check("testAdd", Solution.add(2, 3) == 5);

        // Test 2: multiply(4, 5) == 20
        check("testMultiply", Solution.multiply(4, 5) == 20);

        // Test 3: isEven(4) == true
        check("testIsEvenTrue", Solution.isEven(4) == true);

        // Test 4: isEven(3) == false
        check("testIsEvenFalse", Solution.isEven(3) == false);

        // Test 5: absolute(-5) == 5
        check("testAbsoluteNegative", Solution.absolute(-5) == 5);

        // Test 6: absolute(5) == 5
        check("testAbsolutePositive", Solution.absolute(5) == 5);

        // Test 7: greeting("World") == "Hello World!"
        check("testGreeting", "Hello World!".equals(Solution.greeting("World")));

        System.out.println("SUMMARY " + passed + "/" + (passed + failed));
    }
}
"""


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _count_expected_tests() -> int:
    """Derive expected test count from TestSolution.java fixture."""
    pattern = r'check\("([^"]+)"'
    matches = re.findall(pattern, _TEST_SOLUTION)
    return len(matches)


def _parse_results(stdout: str) -> tuple[int, int]:
    """Parse PASS/FAIL output and return (passed, failed)."""
    passed = 0
    failed = 0
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("PASS "):
            passed += 1
        elif stripped.startswith("FAIL "):
            failed += 1
    return passed, failed


def _javac_available() -> bool:
    """Check if javac is available on the system."""
    try:
        result = subprocess.run(
            ["javac", "-version"],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def validate_java_solution(fixed_code: str, test_fixture: str = _TEST_SOLUTION) -> JavaValidationResult:
    """Validate Java code against the test harness.

    Args:
        fixed_code: The corrected Java source code from the model.
        test_fixture: TestSolution.java test harness content.

    Returns:
        JavaValidationResult with scoring information.
    """
    expected_count = _count_expected_tests()

    # Check javac availability
    if not _javac_available():
        return JavaValidationResult(
            total_tests=expected_count,
            passed_tests=0,
            failed_tests=expected_count,
            score=0.0,
            passed=False,
            compile_success=False,
            exit_code=None,
            timed_out=False,
            stdout="",
            stderr="",
            error="javac not found on system path",
        )

    tmp_dir = None
    try:
        # Create isolated temp directory
        tmp_dir = tempfile.mkdtemp(prefix="java_validate_")
        solution_path = Path(tmp_dir) / "Solution.java"
        test_path = Path(tmp_dir) / "TestSolution.java"

        # Write files
        solution_path.write_text(fixed_code, encoding="utf-8")
        test_path.write_text(test_fixture, encoding="utf-8")

        # Compile
        compile_proc = subprocess.run(
            ["javac", "Solution.java", "TestSolution.java"],
            cwd=tmp_dir,
            capture_output=True,
            text=True,
            timeout=COMPILE_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )

        if compile_proc.returncode != 0:
            # Compilation failed
            return JavaValidationResult(
                total_tests=expected_count,
                passed_tests=0,
                failed_tests=expected_count,
                score=0.0,
                passed=False,
                compile_success=False,
                exit_code=compile_proc.returncode,
                timed_out=False,
                stdout=compile_proc.stdout or "",
                stderr=compile_proc.stderr or "",
            )

        # Run
        run_proc = subprocess.run(
            ["java", "TestSolution"],
            cwd=tmp_dir,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )

        timed_out = False
        if run_proc.returncode == -9 or (hasattr(subprocess, "TimeoutExpired") and False):
            timed_out = True
            return JavaValidationResult(
                total_tests=expected_count,
                passed_tests=0,
                failed_tests=expected_count,
                score=0.0,
                passed=False,
                compile_success=True,
                exit_code=None,
                timed_out=True,
                stdout=run_proc.stdout or "",
                stderr=run_proc.stderr or "",
            )

        stdout = run_proc.stdout or ""
        stderr = run_proc.stderr or ""
        passed, failed = _parse_results(stdout)
        total = passed + failed

        # Fallback to expected count if no markers found
        if total == 0:
            total = expected_count
            failed = expected_count

        score = passed / total if total > 0 else 0.0

        return JavaValidationResult(
            total_tests=total,
            passed_tests=passed,
            failed_tests=failed,
            score=round(score, 4),
            passed=passed == total,
            compile_success=True,
            exit_code=run_proc.returncode,
            timed_out=False,
            stdout=stdout,
            stderr=stderr,
        )

    except subprocess.TimeoutExpired:
        return JavaValidationResult(
            total_tests=expected_count,
            passed_tests=0,
            failed_tests=expected_count,
            score=0.0,
            passed=False,
            compile_success=True,
            exit_code=None,
            timed_out=True,
            stdout="",
            stderr="",
        )

    except Exception as e:
        return JavaValidationResult(
            total_tests=expected_count,
            passed_tests=0,
            failed_tests=expected_count,
            score=0.0,
            passed=False,
            compile_success=True,
            exit_code=None,
            timed_out=False,
            stdout="",
            stderr="",
            error=str(e),
        )

    finally:
        # Cleanup
        if tmp_dir:
            import shutil
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except OSError:
                pass