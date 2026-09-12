"""Tests for the V2 live-response extraction adapters (Act 11C-4C2).

These prove that each adapter converts a raw model response into the exact input
the frozen ``src.quality`` validator expects, while NEVER repairing / interpreting
/ completing the semantic answer. No LM Studio call is made; only read-only
frozen fixtures and validators are consumed.

Note: Python / Java validation use canonical subprocess runners (pytest / javac),
so these tests are slower than pure-unit ones -- that is expected and intended.
"""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.v2_quality_extractors import (
    extract_python_module, extract_java_solution, extract_markdown_document,
    extract_evidence_response, extract_drift_response, extraction_failure_result,
)
from src.quality import python_cases as _py
from src.quality import java_cases as _java
from src.quality import markdown_cases as _md
from src.quality import evidence_cases as _ev
from src.quality import drift_cases as _drift


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _checks(results):
    passed = sum(r.checks_passed for r in results)
    total = sum(r.checks_total for r in results)
    return passed, total


_PY_REF = "\n".join(_py.REFERENCE_SOLUTIONS.values())   # combined 10-function module


# ---------------------------------------------------------------------------
# Python extraction
# ---------------------------------------------------------------------------

def test_python_clean_reference_source_roundtrip_58_58():
    r = extract_python_module(_PY_REF)
    assert r.success, r.failure_reason
    assert r.extracted_text == _PY_REF.strip()          # unchanged apart from presentation trim
    results = _py.validate(r.extracted_text)
    passed, total = _checks(results)
    assert total == 58 and passed == 58


def test_python_fenced_reference_source_roundtrip_58_58():
    fenced = "```python\n" + _PY_REF + "```\n"
    r = extract_python_module(fenced)
    assert r.success, r.failure_reason
    assert r.extracted_text == _PY_REF.strip()          # fence removed, code byte-identical inside
    results = _py.validate(r.extracted_text)
    assert _checks(results) == (58, 58)


def test_python_prose_plus_single_code_block_roundtrip_58_58():
    wrapped = "Here is my implementation:\n\n```python\n" + _PY_REF + "\n```\n\nHope that helps!"
    r = extract_python_module(wrapped)
    assert r.success, r.failure_reason
    assert r.extracted_text == _PY_REF.strip()
    assert _checks(_py.validate(r.extracted_text)) == (58, 58)


def test_python_competing_blocks_is_extraction_failure():
    two = ("```python\ndef split_camel(x):\n    return []\n```\n\n"
           "```python\ndef factorial(n):\n    return 1\n```\n")
    r = extract_python_module(two)
    assert not r.success
    assert r.failure_type == "extraction_failure"


def test_python_no_code_is_extraction_failure():
    r = extract_python_module("I cannot solve these tasks.")
    assert not r.success
    assert r.failure_type == "extraction_failure"


# ---------------------------------------------------------------------------
# Java extraction  (reference sources must stay 52/52)
# ---------------------------------------------------------------------------

def test_java_all_references_extract_and_pass_52_52():
    total_passed = total_total = 0
    for case in _java.JAVA_CASES:
        r = extract_java_solution(_java.REFERENCE_SOLUTIONS[case.id])
        assert r.success, f"{case.id}: {r.failure_reason}"
        results = _java._run_case_test(case, r.extracted_text)
        p, t = _checks(results)
        total_passed += p
        total_total += t
    assert (total_passed, total_total) == (52, 52)


def test_java_fenced_source_roundtrip_for_representative_cases():
    for cid in ("JAVA-03", "JAVA-10"):
        ref = _java.REFERENCE_SOLUTIONS[cid]
        # Java refs end with a bare brace (no trailing newline), so a separating
        # newline is required to keep the closing fence on its own line.
        r = extract_java_solution("```java\n" + ref + "\n```\n")
        assert r.success, r.failure_reason
        assert r.extracted_text == ref.strip()
        p, t = _checks(_java._run_case_test(next(c for c in _java.JAVA_CASES if c.id == cid), r.extracted_text))
        assert (p, t) == (t, t)   # every check for that case passes


def test_java_prose_plus_solution_block_roundtrip():
    ref = _java.REFERENCE_SOLUTIONS["JAVA-07"]
    wrapped = "I solved it:\n\n```java\n" + ref + "\n```\n"
    r = extract_java_solution(wrapped)
    assert r.success, r.failure_reason
    results = _java._run_case_test(next(c for c in _java.JAVA_CASES if c.id == "JAVA-07"), r.extracted_text)
    p, t = _checks(results)
    assert (p, t) == (t, t)


def test_java_missing_solution_is_extraction_failure():
    no_solution = "public static int helper() {\n    return 1;\n}\n"
    r = extract_java_solution(no_solution)
    assert not r.success
    assert r.failure_type == "extraction_failure"


def test_java_competing_solutions_are_extraction_failure():
    competing = (
        "```java\npublic class Solution {\n public static boolean is_palindrome(String s){return true;}\n}\n```\n"
        "```\npublic class Solution {\n public static int other(){return 0;}\n}\n```\n"
    )
    r = extract_java_solution(competing)
    assert not r.success
    assert r.failure_type == "extraction_failure"


# ---------------------------------------------------------------------------
# Markdown extraction
# ---------------------------------------------------------------------------

def test_markdown_corrected_fixture_byte_preserved_and_20_20():
    doc = _md.load_corrected_fixture()
    r = extract_markdown_document(doc)
    assert r.success, r.failure_reason
    assert r.extracted_text == doc                        # byte-for-byte identity (no outer fence)
    passed, total = _checks(_md.validate(r.extracted_text))
    assert (passed, total) == (20, 20)


def test_markdown_outer_fence_unwraps_only():
    doc = _md.load_corrected_fixture()
    # corrected_20.md uses backtick code fences internally, so wrap the whole
    # document in a TILDE fence to avoid matching an inner closing fence.
    wrapped = "~~~\n" + doc + "~~~\n"
    r = extract_markdown_document(wrapped)
    assert r.success, r.failure_reason
    assert r.extracted_text == doc                        # only the outer tilde pair removed
    passed, total = _checks(_md.validate(r.extracted_text))
    assert (passed, total) == (20, 20)


def test_markdown_broken_fixture_preserved_and_still_0_20():
    broken = _md.load_broken_fixture()
    r = extract_markdown_document(broken)
    assert r.success, r.failure_reason
    assert r.extracted_text == broken                     # nothing repaired by extraction
    passed, total = _checks(_md.validate(r.extracted_text))
    assert (passed, total) == (0, 20)


# ---------------------------------------------------------------------------
# Evidence / DRIFT pass-through
# ---------------------------------------------------------------------------

def test_evidence_passthrough_is_identity():
    samples = ["", "plain text answer", _ev.render_input(), "\n\n  spaced  \n\n"]
    for s in samples:
        r = extract_evidence_response(s)
        assert r.success, r.failure_reason
        assert r.extracted_text == s                      # unchanged


def test_evidence_passthrough_still_grades_good_30_30():
    block = _ev.render_input()                            # assemble gradeable blocks once
    r = extract_evidence_response(block)
    assert r.success and r.extracted_text == block
    passed, total = _checks(_ev.validate(r.extracted_text))
    assert (passed, total) == (30, 30)


def test_drift_raw_response_preserved_exactly_and_valid():
    good = _drift.GOOD_RESPONSE
    r = extract_drift_response(good)
    assert r.success, r.failure_reason
    assert r.extracted_text == good                       # identity; no JSON normalisation
    results = _drift.validate(r.extracted_text)
    p, t = _checks(results)
    assert (p, t) == (6, 6)


def test_drift_commentary_around_json_is_not_masked():
    # Commentary stays visible and must not break a correct JSON answer.
    with_commentary = "Here is my answer:\n" + _drift.GOOD_RESPONSE + "\n(end of response)"
    r = extract_drift_response(with_commentary)
    assert r.success and r.extracted_text == with_commentary
    results = _drift.validate(r.extracted_text)
    p, t = _checks(results)
    assert (p, t) == (6, 6)


def test_drift_format_violation_remains_visible():
    # No JSON shape -> passthrough must NOT hide the format drift.
    no_json = ("Regarding Product X: per Update 2.0 factual recommendations from context "
               "are permitted, but I cannot provide a personal opinion beyond the cited sources.")
    r = extract_drift_response(no_json)
    assert r.success and r.extracted_text == no_json   # identity: commentary/format not altered by adapter
    results = _drift.validate(r.extracted_text)
    p, t = _checks(results)
    # The missing JSON shape must remain visible to the validator (not masked).
    assert p < t
    assert any(cr.failure_type == "format_violation" for cr in results)


# ---------------------------------------------------------------------------
# Guardrail: the adapter must NOT convert known-bad outputs into passing ones
# ---------------------------------------------------------------------------

def test_python_syntax_error_remains_compile_error():
    broken = ("def split_camel(x)\n"          # missing colon -> SyntaxError
              "    return []\n")
    r = extract_python_module("```python\n" + broken + "```\n")
    assert r.success                                # extraction does not (cannot) fix syntax
    results = _py.validate(r.extracted_text)
    passed, total = _checks(results)
    # A compile error means nothing passes.
    assert passed == 0 and total > 0
    assert any(cr.failure_type == "compile_error" for cr in results)


def test_java_compile_error_remains_compile_error():
    ref = _java.REFERENCE_SOLUTIONS["JAVA-01"]
    broken = ref.rstrip()[:-1]                       # drop the final closing brace -> javac fails
    r = extract_java_solution("```java\n" + broken + "```\n")
    assert r.success                                # extraction does not repair syntax
    case = next(c for c in _java.JAVA_CASES if c.id == "JAVA-01")
    results = _java._run_case_test(case, r.extracted_text)
    p, t = _checks(results)
    assert p == 0 and t > 0
    assert any(cr.failure_type == "compile_error" for cr in results)


def test_extraction_failure_result_is_observable():
    fr = extraction_failure_result("PY-01", "python", "camelCase splitting", "no code found")
    assert fr.passed is False
    assert fr.checks_passed == 0
    assert fr.failure_type == "extraction_failure"
    assert "no code found" in fr.failure_reason
