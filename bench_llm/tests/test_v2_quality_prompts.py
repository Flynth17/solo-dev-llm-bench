"""Focused tests for the canonical V2 live-stimulus spec layer (Act 11C-4C1).

Proves, structurally and without touching LM Studio / validators / persistence:
* exactly 10 Python specs and 12 Java specs, one Markdown repair prompt
* Evidence reuses all 10 existing frozen scenarios verbatim
* DRIFT reuses the existing canonical render_scenario() verbatim
* expected live-request count == 25 (and its per-suite decomposition)
* prompt text does NOT contain the complete hidden test representations
  (no serialization of Check.args / Check.expected, no answer keys)
* each spec carries the behavioural dimensions the frozen checks exercise
  (so nothing required-by-checks is omitted where omission would be ambiguous,
  and no spec contradicts the expected symbol contract)
"""

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.v2_quality_prompts import (  # noqa: E402
    PYTHON_SPECS, JAVA_SPECS, MARKDOWN_REPAIR_INSTRUCTION,
    evidence_scenarios, drift_rendered_scenario, live_request_topology,
    EXPECTED_LIVE_REQUESTS,
)
from src.quality import python_cases as _py  # noqa: E402 (frozen reference for leakage/coverage)
from src.quality import java_cases as _java  # noqa: E402 (frozen reference)
from src.quality import evidence_cases as _ev  # noqa: E402 (frozen evidence scenarios)
from src.quality._common import Check  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers over frozen checks.
# ---------------------------------------------------------------------------

def _norm_checks(case):
    ck = case.checks
    return [ck] if isinstance(ck, Check) else list(ck)


def _py_call_sentinels():
    """Canonical `func(args)` call forms for every frozen Python assertion.

    These encode the concrete input vectors; a leaked prompt would contain them.
    """
    out = []
    for case in _py.PY_CASES:
        for chk in _norm_checks(case):
            args = ", ".join(repr(a) for a in chk.args)
            out.append(f"{chk.func}({args})")
    return out


def _java_call_sentinels():
    out = []
    for case in _java.JAVA_CASES:
        for chk in _norm_checks(case):
            args = ", ".join(repr(a) for a in chk.args)
            out.append(f"{chk.func}({args})")
    return out


def _py_expected_collection_literals():
    """Repr of collection-valued expected results (lists/dicts) -- pure output vectors."""
    lits = []
    for case in _py.PY_CASES:
        for chk in _norm_checks(case):
            if isinstance(chk.expected, (list, dict)):
                lits.append(repr(chk.expected))
    return lits


# Python spec text joined (for leakage + coverage assertions).
_PY_TEXT = "\n".join(
    f"{s.id} {s.function_name} {s.signature}\n{s.behaviour}\n{s.edge_cases}\n{s.output_requirement}"
    for s in PYTHON_SPECS
).lower()

# Java spec text joined.
_JAVA_TEXT = "\n".join(
    f"{s.id} {s.method_signature}\n{s.behaviour}\n{s.edge_cases}\n{s.output_restriction}"
    for s in JAVA_SPECS
).lower()


# Required behavioural dimensions each Python spec must state (OR-groups: any one
# satisfying the clause is enough). Covers behaviours the hidden checks exercise.
_PY_REQUIRED_DIMENSIONS = {
    "PY-01": [["split_camel"], ["word", "token"], ["uppercase", "upper"],
              ["lowercase", "lower"], ["empty"], ["acronym", "run of uppercase"],
              ["underscore", "non-alphabetic"]],
    "PY-02": [["unique_preserve_order"], ["duplicate", "remov"],
              ["first occurrence", "preserve", "order"], ["empty"]],
    "PY-03": [["nested_sum"], ["sum"], ["nest", "recurse", "nested", "depth"],
              ["inner"], ["negative"]],
    "PY-04": [["factorial"], ["factorial", "product"],
              ["0 and 1", "empty product"], ["negative", "None"]],
    "PY-05": [["partition_parity"], ["even", "odd"], ["keys", "both keys"],
              ["order"], ["zero", "negative"]],
    "PY-06": [["apply_operations"], ["add", "subtract", "multiply"],
              ["order", "sequential"], ["no operations", "initial value"]],
    "PY-07": [["is_valid_email"], ["@"], ["domain"], ["leading", "trailing", "dot"], ["space"]],
    "PY-08": [["safe_int_parse"], ["base-ten", "integer"], ["strip", "whitespace"],
              ["decimal point", "hexadecimal", "None"]],
    "PY-09": [["classify_temperature"], ["solid", "liquid", "gas"],
              ["but not including", "inclusive", "below 0", "100 and above"]],
    "PY-10": [["second_largest"], ["largest", "distinct"], ["duplicate"],
              ["fewer than two", "None"]],
}

# Required behavioural dimensions each Java spec must state.
_JAVA_REQUIRED_DIMENSIONS = {
    "JAVA-01": [["is_palindrome"], ["case-insensitive", "lower"], ["null", "empty"]],
    "JAVA-02": [["groupByFirstLetter"], ["first letter", "lowercased"],
                ["skip null", "null or empty"], ["insertion order", "LinkedHashMap"]],
    "JAVA-03": [["classifySeverity"], ["DEBUG"], ["UNKNOWN"]],
    "JAVA-04": [["safe_length"], ["null"], ["0"]],
    "JAVA-05": [["safe_divide"], ["zero"], ["null"]],
    "JAVA-06": [["distance"], ["euclidean"]],
    "JAVA-07": [["sortByLengthThenLexicographic"], ["length"], ["lexicograp", "tie"], ["stable"]],
    "JAVA-08": [["flattenNested"], ["flatten", "row"], ["null rows", "empty sublist"]],
    "JAVA-09": [["runOperations"], ["inc", "dec", "reset"], ["unrecognized"]],
    "JAVA-10": [["daysBetween"], ["MONDAY"], ["-1"], ["cyclic"]],
    "JAVA-11": [["allWithin"], ["inclusive"], ["empty", "vacuous"]],
    "JAVA-12": [["parseRecords"], ["colon"], ["malformed"], ["integer"]],
}


# ---------------------------------------------------------------------------
# Counts / topology
# ---------------------------------------------------------------------------

def test_exactly_10_python_specs():
    assert len(PYTHON_SPECS) == 10
    ids = [s.id for s in PYTHON_SPECS]
    assert ids == [c.id for c in _py.PY_CASES]  # mirror frozen PY_CASES exactly (incl. PY-10)
    # Every spec is fully populated.
    for s in PYTHON_SPECS:
        assert s.function_name and s.signature and s.behaviour and s.edge_cases and s.output_requirement


def test_exactly_12_java_specs():
    assert len(JAVA_SPECS) == 12
    ids = [s.id for s in JAVA_SPECS]
    assert ids == [c.id for c in _java.JAVA_CASES]  # mirror frozen JAVA_CASES exactly (incl. JAVA-12)
    for s in JAVA_SPECS:
        assert s.method_signature and s.behaviour and s.edge_cases and "compilable java source" in s.output_restriction.lower()


def test_single_markdown_repair_prompt():
    # Exactly one canonical repair instruction is authored.
    assert MARKDOWN_REPAIR_INSTRUCTION.strip().startswith("You are repairing a Markdown document")
    # It enumerates all 20 defect rules without revealing the corrected file.
    for n in range(1, 21):
        assert f"MD-{n:02d}" in MARKDOWN_REPAIR_INSTRUCTION
    # It never hands over the answer fixture content.
    from src.quality import markdown_cases as _md
    assert _md.load_corrected_fixture() not in MARKDOWN_REPAIR_INSTRUCTION


def test_evidence_reuses_all_10_frozen_scenarios_verbatim():
    scenarios = evidence_scenarios()
    frozen = _ev.EVID_FIXTURES
    # Identity: same keys, identical verbatim strings (no rewrite).
    assert set(scenarios) == set(frozen)
    for cid in frozen:
        assert scenarios[cid] == frozen[cid].get("scenario", "")
    assert len(scenarios) == 10
    assert all(scenarios[cid] for cid in scenarios)  # each scenario has real wording


def test_drift_reuses_canonical_scenario_verbatim():
    from src.quality import drift_cases as _d
    # Verbatim reuse: identical to the frozen factory output (no filler).
    assert drift_rendered_scenario() == _d.render_scenario("")
    assert _d.POLICY in drift_rendered_scenario()  # policy block intact, not simplified
    # JSON-only output contract is stated by the canonical scenario itself.
    assert "json only" in drift_rendered_scenario().lower()


def test_expected_live_request_count_is_25():
    topo = live_request_topology()
    assert topo["python"] == 1          # all specs merged into one module request
    assert topo["java"] == 12           # one independent per-case request
    assert topo["markdown"] == 1        # one full-document repair -> all defect results
    assert topo["evidence"] == 10       # one per scenario, independently graded
    assert topo["drift"] == 1           # one instruction block -> one JSON response
    assert topo["total"] == 25
    assert EXPECTED_LIVE_REQUESTS == 25


# ---------------------------------------------------------------------------
# Hidden-test leakage safeguards.
# ---------------------------------------------------------------------------

def test_python_prompt_does_not_serialize_hidden_assertions():
    # Sentinels encode the concrete input call forms and collection-valued
    # expected results of every frozen Python assertion. The prompt must not
    # contain any of them (compared case-insensitively, since _PY_TEXT is lower).
    leaked = [s.lower() for s in (_py_call_sentinels() + _py_expected_collection_literals())]
    assert not any(sentinel in _PY_TEXT for sentinel in leaked), \
        "Python prompt leaks concrete input vectors / expected outputs"


def test_java_prompt_does_not_serialize_hidden_assertions():
    leaked = _java_call_sentinels()
    assert not any(sentinel in _JAVA_TEXT.lower() for sentinel in leaked), \
        "Java prompt leaks concrete input vectors"


def test_no_answer_key_or_reference_impl_in_module_source():
    src = (Path(__file__).parent.parent / "src" / "v2_quality_prompts.py").read_text(encoding="utf-8")
    lowered = src.lower()
    for forbidden in ("reference_solutions", "_run_case_test", "import requests",
                      "import httpx", "from src.results", "import sqlite", "lmstudio"):
        assert forbidden not in lowered, f"stimulus layer must not carry {forbidden}"


# ---------------------------------------------------------------------------
# Semantic coverage: no required-by-checks behaviour is omitted.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid,dim_sets", list(_PY_REQUIRED_DIMENSIONS.items()))
def test_python_spec_states_required_dimensions(sid, dim_sets):
    spec = next(s for s in PYTHON_SPECS if s.id == sid)
    # The required symbol is declared via the signature; include it so the
    # contract (symbol + behaviour) is fully covered.
    combined = f"{spec.function_name} {spec.signature}\n{spec.behaviour}\n{spec.edge_cases}\n{spec.output_requirement}".lower()
    for clause in dim_sets:
        # Compare case-insensitively: the prose is lower-cased but some symbols
        # (split_camel etc. are already lower) -- safe for camelCase too.
        assert any(tok.lower() in combined for tok in clause), \
            f"{sid} omits a required behavioural dimension: {clause}"


@pytest.mark.parametrize("sid,dim_sets", list(_JAVA_REQUIRED_DIMENSIONS.items()))
def test_java_spec_states_required_dimensions(sid, dim_sets):
    spec = next(s for s in JAVA_SPECS if s.id == sid)
    # The required symbol is declared via the method signature.
    combined = f"{spec.method_signature}\n{spec.behaviour}\n{spec.edge_cases}\n{spec.output_restriction}".lower()
    for clause in dim_sets:
        # Compare case-insensitively: the prose is lower-cased but some method
        # names (parseRecords, daysBetween, ...) are camelCase.
        assert any(tok.lower() in combined for tok in clause), \
            f"{sid} omits a required behavioural dimension: {clause}"


def test_spec_symbols_match_frozen_function_names():
    # No spec contradicts the expected symbol contract.
    py_syms = {chk.func for c in _py.PY_CASES for chk in _norm_checks(c)}
    assert {s.function_name for s in PYTHON_SPECS} == py_syms
    java_syms = {chk.func for c in _java.JAVA_CASES for chk in _norm_checks(c)}
    assert {s.method_signature.split("(")[0].strip().split()[-1] for s in JAVA_SPECS} == java_syms
