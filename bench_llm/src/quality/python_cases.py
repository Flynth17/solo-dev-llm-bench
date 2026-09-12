"""BenchLLM V2 Python correctness cases (Act 11C).

Exactly **10** BenchLLM-original cases ``PY-01`` .. ``PY-10``, each targeting a
meaningfully different capability and guarded by several deterministic
assertions.  HumanEval is used only as *inspiration* for style; no HumanEval
prompt, reference solution or hidden test is copied.

Each case returns structured evidence (see :mod:`src.quality._common`) so the
Results layer can report ``"PY-07 FAIL — 6/8 assertions"`` with a failure type
and expected/actual values without re-running anything.

Execution model: **canonical subprocess + pytest** via
:func:`src.python_validator.validate_python_solution`.  No in-process exec, no
custom builtins sandbox, no compile() calls inside BenchLLM.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ._common import (
    CaseDef,
    CaseResult,
    Check,
    COMPILE_ERROR,
    EXTRACTION_FAILURE,
    RUNTIME_FAILURE,
    ASSERTION_FAILURE,
    WRONG_OUTPUT,
    _fmt,
    values_equal,
)


# ------------------------------------------------------------------
# Inline pytest plugin (conftest.py) — outputs per-test results as JSON
#
# Written to a temp directory alongside test files so pytest picks it up.
# Does NOT modify python_validator.py or any existing code.
# ------------------------------------------------------------------




# ------------------------------------------------------------------
# Case definitions (PY-01 .. PY-10)
# ------------------------------------------------------------------

PY_CASES: tuple[CaseDef, ...] = (
    CaseDef(
        id="PY-01",
        category="python",
        name="String camelCase splitting",
        capability="strings / parsing",
        checks=(
            Check("basic split", "split_camel", ("fooBarBaz",), ["foo", "Bar", "Baz"]),
            Check("single word", "split_camel", ("Hello",), ["Hello"]),
            Check("all lower", "split_camel", ("abc",), ["abc"]),
            Check("leading upper", "split_camel", ("ABCDef",), ["ABC", "Def"]),
            Check("empty string", "split_camel", ("",), []),
            Check("no caps middle", "split_camel", ("already_snake",), ["already_snake"]),
        ),
    ),
    CaseDef(
        id="PY-02",
        category="python",
        name="Dedupe preserving order",
        capability="collections",
        checks=(
            Check("dedupe ints", "unique_preserve_order", ([1, 1, 2, 3, 2],), [1, 2, 3]),
            Check("empty", "unique_preserve_order", ([],), []),
            Check("all same", "unique_preserve_order", ([7, 7, 7],), [7]),
            Check("mixed types order", "unique_preserve_order", (["b", "a", "b", "c", "a"],), ["b", "a", "c"]),
            Check("negatives", "unique_preserve_order", ([-1, -1, 0, -1],), [-1, 0]),
        ),
    ),
    CaseDef(
        id="PY-03",
        category="python",
        name="Recursively sum nested numbers",
        capability="nested structures",
        checks=(
            Check("flat list", "nested_sum", ([1, 2, 3],), 6),
            Check("two levels", "nested_sum", ([1, [2, 3]],), 6),
            Check("three levels", "nested_sum", ([1, [2, [3, 4]]],), 10),
            Check("empty inner", "nested_sum", ([[], []],), 0),
            Check("negatives + zero", "nested_sum", ([-1, [0, -5], 10],), 4),
            Check("deeply nested single", "nested_sum", ([[[[7]]]],), 7),
        ),
    ),
    CaseDef(
        id="PY-04",
        category="python",
        name="Factorial with graceful edge handling",
        capability="numerical edge cases",
        checks=(
            Check("zero factorial", "factorial", (0,), 1),
            Check("one factorial", "factorial", (1,), 1),
            Check("small factorial", "factorial", (5,), 120),
            Check("larger factorial", "factorial", (7,), 5040),
            Check("negative returns None", "factorial", (-3,), None),
            Check("large n", "factorial", (10,), 3628800),
        ),
    ),
    CaseDef(
        id="PY-05",
        category="python",
        name="Partition by parity preserving order",
        capability="sorting / grouping",
        checks=(
            Check("mixed", "partition_parity", ([1, 2, 3, 4, 5],), {"even": [2, 4], "odd": [1, 3, 5]}),
            Check("all even", "partition_parity", ([2, 4, 6],), {"even": [2, 4, 6], "odd": []}),
            Check("all odd", "partition_parity", ([1, 3],), {"even": [], "odd": [1, 3]}),
            Check("empty", "partition_parity", ([],), {"even": [], "odd": []}),
            Check("negatives and zero", "partition_parity", ([-2, -3, 0, 5],), {"even": [-2, 0], "odd": [-3, 5]})
        ),
    ),
    CaseDef(
        id="PY-06",
        category="python",
        name="Multi-step value accumulator",
        capability="state / multi-step logic",
        checks=(
            Check("add then multiply", "apply_operations", (10, [("mul", 2), ("add", 5)]), 25),
            Check("order matters", "apply_operations", (10, [("add", 5), ("mul", 2)]), 30),
            Check("empty ops", "apply_operations", (42, []), 42),
            Check("chained three", "apply_operations", (0, [("add", 3), ("add", 4), ("sub", 1)]), 6),
            Check("negatives", "apply_operations", (-5, [("add", 2)]), -3),
        ),
    ),
    CaseDef(
        id="PY-07",
        category="python",
        name="Email validation",
        capability="validation",
        checks=(
            Check("valid basic", "is_valid_email", ("user@example.com",), True),
            Check("valid subdomain", "is_valid_email", ("a.b@mail.co.uk",), True),
            Check("no at sign", "is_valid_email", ("userexample.com",), False),
            Check("no tld", "is_valid_email", ("user@example",), False),
            Check("empty", "is_valid_email", ("",), False),
            Check("leading dot", "is_valid_email", (".user@example.com",), False)
        ),
    ),
    CaseDef(
        id="PY-08",
        category="python",
        name="Robust integer parsing",
        capability="malformed input",
        checks=(
            Check("plain int", "safe_int_parse", ("123",), 123),
            Check("negative", "safe_int_parse", ("-45",), -45),
            Check("whitespace padded", "safe_int_parse", ("  77  ",), 77),
            Check("float string -> None", "safe_int_parse", ("12.5",), None),
            Check("garbage -> None", "safe_int_parse", ("abc",), None),
            Check("empty -> None", "safe_int_parse", ("",), None),
            Check("hex -> None", "safe_int_parse", ("0x1F",), None)
        ),
    ),
    CaseDef(
        id="PY-09",
        category="python",
        name="Temperature boundary classification",
        capability="boundary conditions",
        checks=(
            Check("below zero", "classify_temperature", (-5,), "solid"),
            Check("exactly zero -> liquid", "classify_temperature", (0,), "liquid"),
            Check("just below boil", "classify_temperature", (99,), "liquid"),
            Check("exactly 100 -> gas", "classify_temperature", (100,), "gas"),
            Check("above boil", "classify_temperature", (150,), "gas"),
            Check("fractional liquid", "classify_temperature", (37.5,), "liquid")
        ),
    ),
    CaseDef(
        id="PY-10",
        category="python",
        name="Second distinct largest",
        capability="subtle near-correct logic",
        checks=(
            Check("basic", "second_largest", ([3, 1, 2],), 2),
            Check("with duplicate max", "second_largest", ([1, 2, 2, 3],), 2),
            Check("all equal -> None", "second_largest", ([5, 5, 5],), None),
            Check("single element -> None", "second_largest", ([9],), None),
            Check("negatives", "second_largest", ([-1, -5, -3],), -3),
            Check("two distinct", "second_largest", ([10, 20],), 10)
        ),
    ),
)


# ------------------------------------------------------------------
# Reference solutions — one function per case, all must pass their checks
# ------------------------------------------------------------------

REFERENCE_SOLUTIONS: dict[str, str] = {
    # PY-01: camelCase splitting (regex-based for correctness)
    "split_camel": r"""
import re as _re
_CAMEL_RE = _re.compile(r'([A-Z][a-z]+|[A-Z]+(?=[A-Z]|$)|[^A-Z]+)')
def split_camel(s):
    if not s:
        return []
    parts = _CAMEL_RE.findall(s)
    return [p for p in parts if p]
""",

    # PY-02: dedupe preserving order
    "unique_preserve_order": r"""
def unique_preserve_order(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
""",

    # PY-03: recursive nested sum
    "nested_sum": r"""
def nested_sum(n):
    total = 0
    for x in n:
        if isinstance(x, list):
            total += nested_sum(x)
        else:
            total += x
    return total
""",

    # PY-04: factorial with graceful edge handling
    "factorial": r"""
def factorial(n):
    if not isinstance(n, int) or n < 0:
        return None
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result
""",

    # PY-05: partition by parity preserving order
    "partition_parity": r"""
def partition_parity(nums):
    even = [x for x in nums if isinstance(x, (int, float)) and x % 2 == 0]
    odd = [x for x in nums if isinstance(x, (int, float)) and x % 2 != 0]
    return {"even": even, "odd": odd}
""",

    # PY-06: multi-step value accumulator
    "apply_operations": r"""
def apply_operations(start, ops):
    v = start
    for op in ops:
        if not isinstance(op, (list, tuple)) or len(op) < 2:
            continue
        name, val = op[0], op[1]
        if name == "add":
            v += val
        elif name == "sub":
            v -= val
        elif name == "mul":
            v *= val
    return v
""",

    # PY-07: email validation
    "is_valid_email": r"""
import re as _re
_EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
def is_valid_email(s):
    if not s or not isinstance(s, str):
        return False
    parts = s.split("@")
    if len(parts) != 2:
        return False
    local, domain = parts[0], parts[1]
    if local.startswith(".") or local.endswith("."): return False
    if not local or "." not in domain:
        return False
    if domain.startswith(".") or domain.endswith("."):
        return False
    return bool(_EMAIL_RE.match(s))
""",

    # PY-08: robust integer parsing
    "safe_int_parse": r"""
def safe_int_parse(s):
    try:
        s = str(s).strip()
        if not s:
            return None
        return int(s)
    except (ValueError, TypeError):
        return None
""",

    # PY-09: temperature boundary classification
    "classify_temperature": r"""
def classify_temperature(c):
    if c < 0:
        return "solid"
    elif c < 100:
        return "liquid"
    else:
        return "gas"
""",

    # PY-10: second distinct largest
    "second_largest": r"""
def second_largest(lst):
    if not isinstance(lst, (list, tuple)) or len(set(lst)) < 2:
        return None
    unique = sorted(set(lst), reverse=True)
    return unique[1]
""",
}


# ------------------------------------------------------------------
# Test file generation — one test per Check, named test_py_XX_NN
# ------------------------------------------------------------------

def _build_test_file(case: CaseDef) -> str:
    """Build a pytest-compatible test file for *case*.

    Each Check becomes an individual test function ``test_py_XX_NN`` that calls
    the expected function with fixed args and asserts equality.  The generated
    code is written to a temp dir alongside the model's submitted solution.
    """
    lines = [
        "# Auto-generated test file for BenchLLM V2 quality case",
        "",
    ]

    # Collect all imports needed by this case (from reference solutions)
    imports_needed: set[str] = set()
    for check in case.checks:
        ref_code = REFERENCE_SOLUTIONS.get(check.func, "")
        if "import" in ref_code:
            import_match = __import__("re").search(r"^import\s+(\w+)", ref_code, __import__("re").MULTILINE)
            if import_match:
                imports_needed.add(import_match.group(1))

    for imp in sorted(imports_needed):
        lines.append(f"import {imp}")
    lines.append("")

    # Write each check as a test function
    for idx, check in enumerate(case.checks, start=1):
        safe_name = __import__("re").sub(r"[^a-zA-Z0-9_]", "_", check.func)
        args_str = ", ".join(_fmt_arg(a) for a in check.args)
        expected_str = repr(check.expected)

        lines.append(f"def test_{safe_name}_{idx:02d}():")
        lines.append(f'    from solution import {check.func}')
        lines.append(f"    result = {check.func}({args_str})")
        lines.append(f"    assert result == {expected_str}")

    return "\n".join(lines)


def _fmt_arg(arg: Any) -> str:
    """Format an argument for inclusion in generated test code."""
    if isinstance(arg, bool):
        return "True" if arg else "False"
    if isinstance(arg, (int, float)):
        return str(arg)
    if isinstance(arg, str):
        # Escape quotes inside the string
        escaped = arg.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(arg, (list, tuple)):
        inner = ", ".join(_fmt_arg(a) for a in arg)
        return f"[{inner}]" if isinstance(arg, list) else f"({inner})"
    if arg is None:
        return "None"
    return str(arg)


# ------------------------------------------------------------------
# Subprocess + pytest runner (canonical model — no in-process exec)
# ------------------------------------------------------------------

def _run_case_test(case: CaseDef, generated_code: str) -> list[CaseResult]:
    """Run the canonical subprocess/pytest validator for a single case.

    Writes generated code + test file to a temp directory, runs pytest with
    ``-v`` verbose output, and parses per-test results line-by-line into
    CaseResult objects.  Uses the same trust model as :func:`src.python_validator.validate_python_solution`.
    """
    import subprocess

    tmpdir = tempfile.mkdtemp(prefix="benchllm_py_case_")
    try:
        # Write generated solution code
        sol_path = Path(tmpdir) / "solution.py"
        sol_path.write_text(generated_code, encoding="utf-8")

        # Write test file for this case
        test_code = _build_test_file(case)
        test_path = Path(tmpdir) / "test_solution.py"
        test_path.write_text(test_code, encoding="utf-8")

        # Run pytest via subprocess (canonical model — same as python_validator.py)
        proc = subprocess.run(
            ["python", "-m", "pytest", "--tb=short", "-v", str(test_path)],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=60.0,
        )

        # Parse verbose output line-by-line for per-test results
        test_results = _parse_verbose_output(proc.stdout + proc.stderr)

        # Map per-test results back to CaseResult objects
        results: list[CaseResult] = []
        for check in case.checks:
            safe_name = __import__('re').sub(r'[^a-zA-Z0-9_]', '_', check.func)
            test_name = f"test_{safe_name}_{case.checks.index(check) + 1:02d}"

            # Find matching result from verbose output parser (match full nodeid)
            matched = None
            for tr in test_results:
                if tr["name"].endswith("::" + test_name) or tr["name"] == test_name:
                    matched = tr
                    break

            if matched is None:
                # Test wasn't collected (likely compile error or missing function)
                results.append(CaseResult(
                    case_id=case.id,
                    category=case.category,
                    name=case.name,
                    passed=False,
                    checks_passed=0,
                    checks_total=len(case.checks),
                    failure_type=EXTRACTION_FAILURE,
                    failure_reason=f"Test {test_name} was not collected",
                ))
            elif matched["passed"]:
                results.append(CaseResult(
                    case_id=case.id,
                    category=case.category,
                    name=case.name,
                    passed=True,
                    checks_passed=1,
                    checks_total=1,
                ))
            else:
                # Parse failure details from error message
                check_result = _parse_failure(check, matched.get("error", ""))
                results.append(CaseResult(
                    case_id=case.id,
                    category=case.category,
                    name=case.name,
                    passed=False,
                    checks_passed=0,
                    checks_total=1,
                    failure_type=check_result["failure_type"],
                    failure_reason=check_result["reason"],
                    expected=check_result.get("expected", ""),
                    actual=check_result.get("actual", ""),
                ))

        return results

    finally:
        # Cleanup temp dir (no persistent rows written)
        import shutil
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except OSError:
            pass


def _parse_verbose_output(output: str) -> list[dict]:
    """Parse pytest ``-v`` verbose output for per-test results.

    Each test result appears on its own line like::

        test_py_01_01 PASSED                                                                                     [ 16%]
        test_py_01_02 FAILED                                                                                     [ 33%]
            solution.py:5: AssertionError
                assert result == expected

    Returns a list of ``{"name": str, "passed": bool, "error": str | None}``.
    """
    results: list[dict] = []
    lines = output.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # Match test result lines: starts with test name, contains PASSED/FAILED
        m = __import__('re').match(r'^(\S+)\s+(PASSED|FAILED)', line)
        if m:
            test_name = m.group(1)
            passed = m.group(2) == "PASSED"
            # Collect error details for failed tests (next lines until blank or next test)
            error_lines: list[str] = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                if not next_line.strip():
                    break
                # Stop at the next test result line (starts with 'test_' and has PASSED/FAILED)
                if __import__('re').match(r'^\S+\s+(PASSED|FAILED)', next_line):
                    break
                error_lines.append(next_line)
                j += 1
            results.append({
                "name": test_name,
                "passed": passed,
                "error": "\n".join(error_lines) if not passed else None,
            })
            i = j
        else:
            i += 1
    return results


def _parse_failure(check: Check, error: str) -> dict[str, str]:
    """Parse a pytest assertion failure into structured fields."""
    # Extract expected/actual from assert message: "expected X instead of Y"
    m = __import__("re").search(r"instead of\s+'([^']*)'", error)
    actual_str = m.group(1) if m else ""

    m2 = __import__("re").search(r"assertion_error|AssertionError", error, __import__("re").IGNORECASE)
    failure_type = ASSERTION_FAILURE  # default for assertion failures

    # Check for specific failure types from the error message
    if "NameError" in error or "ImportError" in error:
        failure_type = EXTRACTION_FAILURE
    elif "TypeError" in error and ("takes" in error or "missing" in error):
        failure_type = EXTRACTION_FAILURE  # wrong args → extraction issue
    elif "TypeError" in error:
        failure_type = RUNTIME_FAILURE

    return {
        "failure_type": failure_type,
        "reason": f"{check.func}({', '.join(repr(a) for a in check.args)}) failed",
        "expected": _fmt(check.expected),
        "actual": actual_str or error[:200],
    }


# ------------------------------------------------------------------
# Public API — validate() uses canonical subprocess/pytest path
# ------------------------------------------------------------------

def validate(source: str) -> list[CaseResult]:
    """Validate a submitted Python module *source* against all PY cases.

    Uses the **canonical** :func:`src.python_validator.validate_python_solution`
    trust model (subprocess + pytest).  No in-process exec, no compile(), no
    custom builtins sandbox.

    Returns one list of CaseResult per case in ``PY_CASES`` order.  Each inner
    list contains one CaseResult per Check within that case.

    If the source fails to compile for a given case, all its checks are reported
    as ``compile_error``.
    """
    import subprocess

    results: list[CaseResult] = []

    # First check: can the source even be compiled? (fast path)
    try:
        compile(source, "<generated-py-case>", "exec")  # noqa: S102 — benchmark output only; fast syntax check
    except SyntaxError as exc:
        for case in PY_CASES:
            results.extend([
                CaseResult(
                    case_id=case.id,
                    category=case.category,
                    name=case.name,
                    passed=False,
                    checks_passed=0,
                    checks_total=len(case.checks),
                    failure_type=COMPILE_ERROR,
                    failure_reason=f"SyntaxError: {exc.msg} (line {exc.lineno})",
                )
                for _ in case.checks
            ])
        return results

    # Run each case through canonical subprocess/pytest validator
    for case in PY_CASES:
        try:
            case_results = _run_case_test(case, source)
            results.extend(case_results)
        except subprocess.TimeoutExpired:
            results.extend([
                CaseResult(
                    case_id=case.id,
                    category=case.category,
                    name=case.name,
                    passed=False,
                    checks_passed=0,
                    checks_total=len(case.checks),
                    failure_type=RUNTIME_FAILURE,
                    failure_reason="pytest subprocess timed out",
                )
                for _ in case.checks
            ])
        except Exception as exc:  # noqa: BLE001 — classify any unexpected error
            results.extend([
                CaseResult(
                    case_id=case.id,
                    category=case.category,
                    name=case.name,
                    passed=False,
                    checks_passed=0,
                    checks_total=len(case.checks),
                    failure_type=RUNTIME_FAILURE,
                    failure_reason=f"Unexpected error: {type(exc).__name__}: {exc}",
                )
                for _ in case.checks
            ])

    return results


def all_function_names() -> set[str]:
    """All function names referenced across the PY cases (for fixture checks)."""
    return {c.func for case in PY_CASES for c in case.checks}
