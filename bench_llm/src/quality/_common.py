"""Shared types + helpers for the BenchLLM V2 quality suite (Act 11C).

The goal of this package is to answer three questions about a model's output in
a *deterministic* way — no LLM-as-judge:

    1. exactly which cases passed or failed
    2. why a case failed            (``failure_type`` + ``failure_reason``)
    3. what the expected vs actual value was

Every suite exposes two things:

    * ``CASES`` — an ordered list of :class:`CaseDef` describing the stable cases
      for that category (their ``id`` is part of the public contract, e.g.
      ``PY-01`` .. ``PY-10``).
    * a ``validate(...)`` function that consumes the model's generated artifact
      (code / markdown / textual response) and returns one :class:`CaseResult`
      per case.

The design is deliberately pure and side-effect free so it can be unit tested in
isolation without an LLM endpoint, without touching disk, and without writing to
the active V2 dataset.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


# ------------------------------------------------------------------
# Failure-type taxonomy (never flattened into a generic "FAIL")
# ------------------------------------------------------------------

COMPILE_ERROR = "compile_error"            # generated code did not compile / execute
EXTRACTION_FAILURE = "extraction_failure"  # a required symbol/function is absent
ASSERTION_FAILURE = "assertion_failure"    # an assertion was violated
RUNTIME_FAILURE = "runtime_failure"        # the code raised at runtime on a valid input
WRONG_OUTPUT = "wrong_output"              # produced a value, but not the expected one
TIMEOUT = "timeout"                          # execution exceeded time limit
INSUFFICIENT_RESPONSE = "insufficient_response"  # model refused / gave no usable answer
UNSUPPORTED_INFERENCE = "unsupported_inference"  # model inferred something not supported by evidence
FABRICATION = "fabrication"                # model fabricated a version/value/claim
FALSE_REFUSAL = "false_refusal"            # model refused even though enough evidence existed
FORMAT_VIOLATION = "format_violation"      # output shape/format was wrong (incl. markdown defects)


# ------------------------------------------------------------------
# CaseResult — the granular evidence record for a single case
# ------------------------------------------------------------------

@dataclass(frozen=True)
class CaseResult:
    """Structured, serialisable result for one quality case.

    This is exactly what the Results/reporting layer needs to answer "what
    failed?" without re-running the benchmark.  ``expected``/``actual`` are kept
    as compact string forms (never giant blobs).
    """

    case_id: str
    category: str
    name: str
    passed: bool
    checks_passed: int
    checks_total: int
    failure_type: str = ""
    failure_reason: str = ""
    expected: str = ""
    actual: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------
# CaseDef — the *definition* of a case (what is checked and how)
# ------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    """A single deterministic assertion within a case."""

    label: str
    func: str                       # function/method name expected in generated module
    args: tuple = ()                # positional arguments to call with
    expected: Any = None            # expected return value


@dataclass(frozen=True)
class CaseDef:
    """A stable, named quality case with deterministic checks."""

    id: str
    category: str
    name: str
    capability: str                 # human-readable capability descriptor
    checks: tuple[Check, ...] = ()  # ordered deterministic assertions


def _result_for_case(case: CaseDef) -> CaseResult:
    """Build a fully-failed placeholder result for *case* (used when the case
    cannot even be evaluated — e.g. code failed to compile)."""
    return CaseResult(
        case_id=case.id,
        category=case.category,
        name=case.name,
        passed=False,
        checks_passed=0,
        checks_total=len(case.checks),
        failure_type=COMPILE_ERROR,
        failure_reason="Case could not be evaluated",
    )


def summarize(results: Iterable[CaseResult]) -> dict[str, Any]:
    """Aggregate per-case results into a category-level summary.

    Returns ``{passed, total, score, failed_ids}`` where *score* is derived from
    the individual case pass/fail (never fabricated).  Empty input yields an
    honest "no data" result rather than a zero score.
    """
    results = list(results)
    if not results:
        return {"passed": 0, "total": 0, "score": None, "failed_ids": [], "results": []}
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    failed_ids = [r.case_id for r in results if not r.passed]
    return {
        "passed": passed,
        "total": total,
        "score": round(passed / total, 4) if total else None,
        "failed_ids": failed_ids,
        "results": [r.to_dict() for r in results],
    }


# ------------------------------------------------------------------
# Safe-ish execution helper for code cases (Python + Java source evaluation)
# ------------------------------------------------------------------

def safe_call(fn, args: tuple) -> Any:
    """Call ``fn(*args)`` mapping common failure modes to exceptions.

    The caller inspects the raised exception to set ``failure_type``.  We let
    ``TypeError``/``NameError`` propagate (missing/wrong args -> extraction or
    usage problem) and treat any other exception as a runtime failure.
    """
    try:
        return fn(*args)
    except (TypeError, NameError, AttributeError):
        raise
    except Exception as exc:  # noqa: BLE001 — we classify, not swallow
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc


def _fmt(value: Any) -> str:
    """Compact string form for expected/actual (avoids giant blobs)."""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float, str)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    if value is None:
        return "None"
    s = str(value)
    return s[:200]


def values_equal(expected: Any, actual: Any) -> bool:
    """Equality that treats ``1`` and ``1.0`` as equal (numeric tolerance for
    tokenizer/typed-language quirks) but keeps strings/types distinct otherwise."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        try:
            return abs(float(expected) - float(actual)) < 1e-9
        except (TypeError, ValueError):
            return False
    return expected == actual


# ------------------------------------------------------------------
# Markdown defect detection helpers
# ------------------------------------------------------------------

def _split_lines_keep_eol(text: str) -> list[str]:
    """Split into lines but keep the trailing newline marker so detectors can
    see exact line endings / blank lines."""
    if text == "":
        return []
    parts = text.splitlines(keepends=True)
    # Normalise CRLF/CR -> LF for deterministic detection.
    return [p.replace("\r\n", "\n").replace("\r", "\n") for p in parts]


def _is_heading(line: str) -> bool:
    """True if *line* (without EOL) is an AT-style heading (# .. ######)."""
    stripped = line.rstrip("\n")
    return bool(re.match(r"^\s{0,3}#{1,6}\b", stripped))


def _heading_level(line: str) -> int | None:
    stripped = line.rstrip("\n")
    m = re.match(r"^(\s{0,3})#+", stripped)
    if not m:
        return None
    return len(m.group(0).lstrip())


def _is_blank(line: str) -> bool:
    return line.strip() == ""
