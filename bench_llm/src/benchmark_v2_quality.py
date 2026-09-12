"""V2 quality-corpus orchestration (Act 11C-4A).

Runs the *frozen* V2 quality corpus — Python, Java, Markdown, Evidence and
DRIFT-01 — through their existing ``src.quality`` validators and returns a single
structured, in-memory aggregate.

Design constraints honoured by this module:

* **Reuses the frozen validators verbatim.** This is an integration shim, not a
  second engine. It imports ``python_cases`` / ``java_cases`` / ``markdown_cases``
  / ``evidence_cases`` / ``drift_cases`` and calls their public ``validate()``
  entry points (and Java's per-case subprocess harness). None of the behaviour in
  ``src/quality/`` is read or reimplemented here.

* **No persistence.** This module never imports or touches ``src.results``,
  ``src.routes.*``, SQLite or CSV writers, so it cannot alter the DB schema, CSV
  schema, benchmark rows, archive data or Results UI. It is fully testable in
  memory and has no side effects beyond the subprocesses each validator already
  performs for its own grading.

* **Independent scale from legacy scoring.** ``src/benchmark.py`` (LM Studio
  throughput) and ``src/benchmark_*.py`` (the legacy 60-point quality score) are
  unchanged. V2 corpus results are exposed as ``v2_quality_checks_passed`` /
  ``v2_quality_checks_total`` — 166/166 is *not* conflated with a 60/60 scale.

All aggregate totals are DERIVED from actual ``CaseResult`` objects; no pass/fail
number here is hard-coded.
"""

from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass
from typing import Any, Optional

from src.quality._common import CaseResult
from src.quality import python_cases as _python_cases
from src.quality import java_cases as _java_cases
from src.quality import markdown_cases as _markdown_cases
from src.quality import evidence_cases as _evidence_cases
from src.quality import drift_cases as _drift_cases


# ---------------------------------------------------------------------------
# Suite registry + reference-input factories
# ---------------------------------------------------------------------------
#
# Each entry describes how to exercise one suite against its known-good baseline.
# ``logical_cases`` is derived at runtime from the frozen case definitions, so a
# new case added anywhere updates the aggregate automatically rather than
# requiring a magic number here.
#
# The input factories default to the good-reference corpus but accept overrides
# (via :func:`run_v2_quality_corpus`) so a deliberate failing response can be
# injected for testing without mutating any frozen fixture.

def _python_default_source() -> str:
    # Python functions have unique names and concatenate cleanly; the canonical
    # validator compiles once and runs each case against the combined module.
    return "\n".join(_python_cases.REFERENCE_SOLUTIONS.values())


def _java_per_case_inputs() -> dict[str, str]:
    # Java solutions collide when concatenated (duplicate class/method names), so
    # the only path that compiles is per-case: each case's own solution + harness.
    return {c.id: _java_cases.REFERENCE_SOLUTIONS.get(c.id, "") for c in _java_cases.JAVA_CASES}


def _markdown_default_text() -> str:
    return _markdown_cases.load_corrected_fixture()


def _evidence_default_text() -> str:
    return _evidence_cases.render_input()


def _drift_default_response() -> str:
    return _drift_cases.GOOD_RESPONSE


# (suite_key, human label, logical_cases getter, input-fetcher)
_SUITE_SPECS = [
    ("python", "python", lambda: len(_python_cases.PY_CASES), _python_default_source),
    ("java", "java", lambda: len(_java_cases.JAVA_CASES), _java_per_case_inputs),
    ("markdown", "markdown", lambda: len(_markdown_cases.MARKDOWN_CASES), _markdown_default_text),
    ("evidence", "evidence", lambda: len(_evidence_cases.EVID_CASES), _evidence_default_text),
    ("drift", "drift-01", lambda: len(_drift_cases.DRIFT_CASES), _drift_default_response),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dispatch_validate(spec_key: str, text: str):
    return {
        "python": _python_cases.validate,
        "markdown": _markdown_cases.validate,
        "evidence": _evidence_cases.validate,
        "drift": _drift_cases.validate,
    }[spec_key](text)


def _flatten_results(spec_key: str, inputs: Any) -> list[CaseResult]:
    """Run one suite and return its flat list of ``CaseResult`` objects.

    Java needs per-case invocation (see :func:`_java_per_case_inputs`); every
    other suite exposes a single ``validate(source_or_text)`` entry point.
    Validator subprocess output is silenced so orchestration runs cleanly under a
    test runner and leaves no console noise behind.
    """
    if spec_key == "java":
        flat: list[CaseResult] = []
        for case in _java_cases.JAVA_CASES:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                flat.extend(_java_cases._run_case_test(case, inputs[case.id]))
        return flat

    results = _dispatch_validate(spec_key, inputs)
    if isinstance(results, list) and results and isinstance(results[0], list):
        results = [r for sub in results for r in sub]  # normalise nested -> flat
    return list(results)


def _aggregate(results: list[CaseResult]) -> tuple[int, int]:
    """Sum checks passed / total across every ``CaseResult`` (uniform metric)."""
    checks_passed = sum(r.checks_passed for r in results)
    checks_total = sum(r.checks_total for r in results)
    return checks_passed, checks_total


def _case_result_dict(r: CaseResult) -> dict[str, Any]:
    """Preserve the full granular evidence record for one case."""
    return {
        "case_id": r.case_id,
        "category": r.category,
        "passed": r.passed,
        "checks_passed": r.checks_passed,
        "checks_total": r.checks_total,
        "failure_type": r.failure_type,
        "failure_reason": r.failure_reason,
        "expected": r.expected,
        "actual": r.actual,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_v2_quality_corpus(
    python_source: Optional[str] = None,
    java_inputs: Optional[dict[str, str]] = None,
    markdown_text: Optional[str] = None,
    evidence_text: Optional[str] = None,
    drift_response: Optional[str] = None,
) -> dict[str, Any]:
    """Run the entire frozen V2 quality corpus and return one structured result.

    Each argument optionally overrides that suite's known-good input so a
    deliberately failing response can be injected deterministically (for example
    ``drift_response=_drift_cases.INVENTED_RULE_BAD_RESPONSE``). When an argument
    is omitted the module falls back to the good-reference corpus for that suite.

    Returns a dict with:
        - ``suite``: "v2-quality"
        - ``logical_cases_attempted``: total CaseDef count across all suites (53)
        - ``checks_passed`` / ``checks_total``: derived from every CaseResult
        - ``v2_quality_checks_passed`` / ``v2_quality_checks_total``: alias of the
          two above, kept separate on purpose from the legacy 60-point scale
        - ``suites``: per-suite breakdown (logical_cases, checks_passed,
          checks_total, results[])
        - ``results``: flattened granular CaseResult entries for every suite
    """
    overrides = {
        "python": python_source,
        "java": java_inputs,
        "markdown": markdown_text,
        "evidence": evidence_text,
        "drift": drift_response,
    }

    flat_all: list[CaseResult] = []
    suites_out: dict[str, Any] = {}
    logical_cases_total = 0

    for key, label, logical_getter, input_getter in _SUITE_SPECS:
        cases_count = logical_getter()
        logical_cases_total += cases_count

        override = overrides[key]
        if override is None:
            inputs = input_getter()
        else:
            inputs = override

        results = _flatten_results(key, inputs)
        checks_passed, checks_total = _aggregate(results)
        flat_all.extend(results)

        suites_out[label] = {
            "suite": label,
            "logical_cases": cases_count,
            "checks_passed": checks_passed,
            "checks_total": checks_total,
            "results": [_case_result_dict(r) for r in results],
        }

    total_passed, total_total = _aggregate(flat_all)

    return {
        "suite": "v2-quality",
        "logical_cases_attempted": logical_cases_total,
        "checks_passed": total_passed,
        "checks_total": total_total,
        "v2_quality_checks_passed": total_passed,
        "v2_quality_checks_total": total_total,
        "suites": suites_out,
        "results": [_case_result_dict(r) for r in flat_all],
    }
