"""Tests for the V2 quality-corpus orchestration (Act 11C-4A).

Exercises src/benchmark_v2_quality.run_v2_quality_corpus against the *frozen*
corpus and asserts:

- the good reference corpus yields the full logical-case / check totals derived
  from real CaseResult objects (no hard-coded aggregates),
- a deliberately failing response decreases the aggregate while keeping the exact
  failed case visible with its failure_type / failure_reason intact,
- the orchestrator is pure in-memory: it imports only the frozen validators and
  never touches results / routes / sqlite / csv (so no persistence occurs).

These tests run real subprocess-based validators, so they are slower than unit
tests but verify the true end-to-end behaviour of the integration shim.
"""

import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import benchmark_v2_quality as v2  # noqa: E402
from src.quality import drift_cases as dc  # noqa: E402


def _run(**overrides):
    return v2.run_v2_quality_corpus(**overrides)


def test_good_corpus_totals_derived_from_results():
    r = _run()

    # Structured aggregate shape.
    assert r["suite"] == "v2-quality"
    assert r["logical_cases_attempted"] == 53          # PY(10)+JAVA(12)+MD(20)+EVID(10)+DRIFT(1)
    assert r["checks_passed"] == 166
    assert r["checks_total"] == 166
    # v2 checks exposed separately from any legacy 60-point scale.
    assert r["v2_quality_checks_passed"] == 166
    assert r["v2_quality_checks_total"] == 166

    # Per-suite breakdown matches the frozen corpus definition counts.
    s = r["suites"]
    assert {k: v["logical_cases"] for k, v in s.items()} == {
        "python": 10, "java": 12, "markdown": 20, "evidence": 10, "drift-01": 1,
    }
    assert (s["python"]["checks_passed"], s["python"]["checks_total"]) == (58, 58)
    assert (s["java"]["checks_passed"], s["java"]["checks_total"]) == (52, 52)
    assert (s["markdown"]["checks_passed"], s["markdown"]["checks_total"]) == (20, 20)
    assert (s["evidence"]["checks_passed"], s["evidence"]["checks_total"]) == (30, 30)
    assert (s["drift-01"]["checks_passed"], s["drift-01"]["checks_total"]) == (6, 6)

    # Granular evidence record preserved on every entry.
    required = {
        "case_id", "category", "passed", "checks_passed", "checks_total",
        "failure_type", "failure_reason", "expected", "actual",
    }
    for entry in r["results"]:
        assert required <= set(entry)


def test_failing_response_decreases_and_preserves_evidence():
    good = _run()
    bad = _run(drift_response=dc.REFUSE_FAIL_BAD_RESPONSE)

    # Logical cases and total checks are unaffected; only passed count drops.
    assert bad["logical_cases_attempted"] == good["logical_cases_attempted"] == 53
    assert bad["checks_total"] == good["checks_total"] == 166
    assert bad["checks_passed"] < good["checks_passed"]

    # The exact failed case remains visible with full evidence.
    d_bad = bad["suites"]["drift-01"]["results"][0]
    d_ref = dc.validate(dc.REFUSE_FAIL_BAD_RESPONSE)[0]
    assert d_bad["case_id"] == "DRIFT-01"
    assert d_bad["passed"] is False
    assert (d_bad["checks_passed"], d_bad["failure_type"], d_bad["failure_reason"]) == \
        (d_ref.checks_passed, d_ref.failure_type, d_ref.failure_reason)

    # The four non-injected suites are byte-for-byte identical.
    for k in ("python", "java", "markdown", "evidence"):
        assert bad["suites"][k] == good["suites"][k]


def test_orchestrator_has_no_persistence_imports():
    tree = ast.parse(open(
        os.path.join(os.path.dirname(__file__), "..", "src", "benchmark_v2_quality.py"),
        encoding="utf-8",
    ).read())

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                imported.add(node.module.split(".")[0])

    # No DB / CSV / result-store / route imports => cannot write persistence.
    assert not (imported & {"results", "csv", "sqlite3"})
    assert not {m for m in imported if m.startswith("routes")}
