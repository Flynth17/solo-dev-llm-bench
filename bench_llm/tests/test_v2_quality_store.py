"""Tests for additive V2-quality granular persistence (Act 11C-4B).

Run with: python -m pytest tests/test_v2_quality_store.py -v

Isolation model (same Act-2 pattern as test_sqlite_storage.py): every store is
built against temporary CSV/DB paths, so no active benchmark data, task_runs,
CSV or archive is ever read from or written to during the test run.
"""

import contextlib
import io
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.results import (  # noqa: E402
    ResultsStore,
    compute_configuration_fingerprint,
    classify_run_for_result,
    _DEFAULT_CSV_PATH,
    _DEFAULT_DB_PATH,
)
from src.quality import python_cases as _python_cases  # noqa: E402
from src.quality import java_cases as _java_cases  # noqa: E402
from src.quality import markdown_cases as _markdown_cases  # noqa: E402
from src.quality import evidence_cases as _evidence_cases  # noqa: E402
from src.quality import drift_cases as _drift  # noqa: E402
from src.v2_quality_store import V2QualityStore  # noqa: E402


# ---------------------------------------------------------------------------
# Test-local V2 quality-corpus fixture helper (Act 11C-4B).
#
# Replaces the retired production shim ``src.benchmark_v2_quality`` as a source
# of *valid* V2 run dicts for these store tests. It runs the retained frozen
# ``src.quality`` validators directly and returns the SAME structured dict shape
# that the store persists -- pure test scaffolding, not a production module.
# ---------------------------------------------------------------------------

def _run_v2_quality_corpus(
    python_source: Optional[str] = None,
    java_inputs: Optional[dict[str, str]] = None,
    markdown_text: Optional[str] = None,
    evidence_text: Optional[str] = None,
    drift_response: Optional[str] = None,
) -> dict[str, Any]:
    """Return one valid V2 quality-corpus result dict from the frozen validators.

    Mirrors the retired ``run_v2_quality_corpus`` so every assertion in this file
    keeps protecting the exact store contract (53 logical cases / 141 evidence
    entries / 166 checks for the good corpus, with a real DRIFT failure when an
    override is supplied). No production orchestration module is retained.
    """
    def _default_python():
        return "\n".join(_python_cases.REFERENCE_SOLUTIONS.values())

    def _default_java():
        return {c.id: _java_cases.REFERENCE_SOLUTIONS.get(c.id, "") for c in _java_cases.JAVA_CASES}

    def _default_markdown():
        return _markdown_cases.load_corrected_fixture()

    def _default_evidence():
        return _evidence_cases.render_input()

    specs = [
        ("python", "python", lambda: len(_python_cases.PY_CASES), python_source, _default_python),
        ("java", "java", lambda: len(_java_cases.JAVA_CASES), java_inputs, _default_java),
        ("markdown", "markdown", lambda: len(_markdown_cases.MARKDOWN_CASES), markdown_text, _default_markdown),
        ("evidence", "evidence", lambda: len(_evidence_cases.EVID_CASES), evidence_text, _default_evidence),
        ("drift", "drift-01", lambda: len(_drift.DRIFT_CASES), drift_response, (lambda: _drift.GOOD_RESPONSE)),
    ]

    def _flatten(key, inputs):
        if key == "java":
            flat = []
            for case in _java_cases.JAVA_CASES:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    flat.extend(_java_cases._run_case_test(case, inputs[case.id]))
            return flat
        validator = {"python": _python_cases.validate, "markdown": _markdown_cases.validate,
                     "evidence": _evidence_cases.validate, "drift": _drift.validate}[key]
        res = validator(inputs)
        if isinstance(res, list) and res and isinstance(res[0], list):
            res = [r for sub in res for r in sub]  # normalise nested -> flat
        return list(res)

    def _case_dict(r):
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

    flat_all = []
    suites_out = {}
    logical_total = 0
    for key, label, logical_getter, override, input_getter in specs:
        cases_count = logical_getter()
        logical_total += cases_count
        inputs = override if override is not None else input_getter()
        results = _flatten(key, inputs)
        checks_passed = sum(r.checks_passed for r in results)
        checks_total = sum(r.checks_total for r in results)
        flat_all.extend(results)
        suites_out[label] = {
            "suite": label,
            "logical_cases": cases_count,
            "checks_passed": checks_passed,
            "checks_total": checks_total,
            "results": [_case_dict(r) for r in results],
        }

    total_passed = sum(r.checks_passed for r in flat_all)
    total_total = sum(r.checks_total for r in flat_all)

    return {
        "suite": "v2-quality",
        "logical_cases_attempted": logical_total,
        "checks_passed": total_passed,
        "checks_total": total_total,
        "v2_quality_checks_passed": total_passed,
        "v2_quality_checks_total": total_total,
        "suites": suites_out,
        "results": [_case_dict(r) for r in flat_all],
    }


# ---------------------------------------------------------------------------
# Isolated storage helpers (Act-2 pattern)
# ---------------------------------------------------------------------------

def _temp_paths():
    """Yield isolated (csv, db) paths and clean them up afterwards."""
    tmp_csv = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_csv.close()
    tmp_db.close()
    yield Path(tmp_csv.name), Path(tmp_db.name)
    for p in (Path(tmp_csv.name), Path(tmp_db.name)):
        if p.exists():
            p.unlink()


def _run_ids(db_path: Path, table: str = "v2_quality_runs") -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return [r[0] for r in conn.execute(
            f"SELECT run_id FROM {table} WHERE run_id IS NOT NULL ORDER BY id")]
    finally:
        conn.close()


def _count_rows(db_path: Path, table: str) -> int:
    """Return row count for ``table`` (0 when the table does not exist)."""
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. Good-reference round trip
# ---------------------------------------------------------------------------

def test_good_reference_round_trip():
    """Persist the full good corpus and read it back unchanged."""
    for _, db_p in _temp_paths():
        result = _run_v2_quality_corpus()
        store = V2QualityStore(db_path=db_p)
        run_id = store.save_run(result, run_id="v2-good-ref")

        loaded = store.load_run(run_id)
        assert loaded is not None

        run = loaded["run"]
        # Three distinct concepts preserved at the run level.
        assert run["logical_cases_attempted"] == 53
        assert run["result_entries"] == 141
        assert run["checks_passed"] == 166
        assert run["checks_total"] == 166

        # 141 granular entries survive exactly.
        assert len(loaded["results"]) == 141

        # Per-suite deterministic totals (independent scale, not a 60-point score).
        CAT_KEY = {"python": "python", "java": "java", "markdown": "markdown",
                   "evidence": "evidence", "drift": "drift-01"}
        per_suite = {}
        for entry in loaded["results"]:
            key = CAT_KEY[entry["category"]]
            per_suite.setdefault(key, [0, 0])
            per_suite[key][0] += entry["checks_passed"]
            per_suite[key][1] += entry["checks_total"]

        assert per_suite["python"] == [58, 58]
        assert per_suite["java"] == [52, 52]
        assert per_suite["markdown"] == [20, 20]
        assert per_suite["evidence"] == [30, 30]
        assert per_suite["drift-01"] == [6, 6]

        # Every entry passed; all flags are real booleans after round-trip.
        for entry in loaded["results"]:
            assert entry["passed"] is True
            assert isinstance(entry["passed"], bool)


# ---------------------------------------------------------------------------
# 2. Failing-reference round trip (deliberate DRIFT drift)
# ---------------------------------------------------------------------------

def test_drift_failure_round_trip():
    """Persist a corpus with a injected DRIFT failure; evidence survives."""
    for _, db_p in _temp_paths():
        result = _run_v2_quality_corpus(drift_response=_drift.INVENTED_RULE_BAD_RESPONSE)
        store = V2QualityStore(db_path=db_p)
        run_id = store.save_run(result, run_id="v2-drift-fail")

        loaded = store.load_run(run_id)
        assert loaded is not None

        # Aggregate reflects exactly one dropped check.
        assert loaded["run"]["checks_passed"] == 165
        assert loaded["run"]["checks_total"] == 166

        drift_rows = [e for e in loaded["results"] if e["case_id"] == "DRIFT-01"]
        assert len(drift_rows) == 1
        drift = drift_rows[0]
        assert drift["passed"] is False
        assert (drift["checks_passed"], drift["checks_total"]) == (5, 6)

        # Fabrication-class failure type + reason preserved verbatim.
        assert drift["failure_type"] == "fabrication"
        assert drift["failure_reason"]
        assert "invented" in drift["failure_reason"].lower()

        # Ordering index preserved for every row (repeated case_ids safe).
        seqs = [r["seq"] for r in loaded["results"]]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)


# ---------------------------------------------------------------------------
# 3. Test isolation from active data
# ---------------------------------------------------------------------------

def test_persistence_does_not_touch_active_data():
    """A persisted V2 run must not alter active runs/task_runs/CSV."""
    for tmp_csv, db_p in _temp_paths():
        # Snapshot active-store state BEFORE the isolated write.
        before = _count_rows(_DEFAULT_DB_PATH, "runs")
        v2_before = _count_rows(_DEFAULT_DB_PATH, "v2_quality_results")

        # Snapshot active CSV so we can prove it is untouched byte-for-byte.
        csv_before = _DEFAULT_CSV_PATH.read_text(encoding="utf-8") if _DEFAULT_CSV_PATH.exists() else None

        # Isolated V2 write only touches the temp DB.
        store = V2QualityStore(db_path=db_p)
        rid = store.save_run(_run_v2_quality_corpus(), run_id="v2-isolated")
        assert len(_run_ids(db_p)) == 1
        assert store.load_run(rid)["run"]["logical_cases_attempted"] == 53

        # Active data unchanged afterwards (active DB keeps zero V2 rows).
        assert _count_rows(_DEFAULT_DB_PATH, "runs") == before, "active runs table changed"
        assert _count_rows(_DEFAULT_DB_PATH, "v2_quality_results") == 0, "active DB gained V2 rows"

        # Active CSV untouched byte-for-byte.
        if _DEFAULT_CSV_PATH.exists():
            assert _DEFAULT_CSV_PATH.read_text(encoding="utf-8") == csv_before


# ---------------------------------------------------------------------------
# 4. Schema compatibility + idempotency
# ---------------------------------------------------------------------------

def test_legacy_rows_still_load_and_schema_is_additive():
    """New V2 tables must not disturb legacy runs/metadata; migrations idempotent."""
    for csv_p, db_p in _temp_paths():
        # Legacy store creates its own tables on a fresh temp DB.
        ResultsStore(csv_path=csv_p, db_path=db_p)

        conn = sqlite3.connect(str(db_p))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        assert {"id", "run_id", "timestamp"}.issubset(cols)
        table_names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "runs" in table_names

        # V2 store on the SAME file adds (does not replace) tables.
        v2 = V2QualityStore(db_path=db_p)
        rid = v2.save_run(_run_v2_quality_corpus(), run_id="v2-compat")
        conn = sqlite3.connect(str(db_p))
        table_names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        runs_rows_before = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        conn.close()

        assert {"v2_quality_runs", "v2_quality_results"}.issubset(table_names)

        # Idempotent re-init: create a second store on the same file.
        V2QualityStore(db_path=db_p)
        assert len(_run_ids(db_p)) == 1

        # Legacy runs count unchanged by V2 writes; V2 row still readable.
        conn = sqlite3.connect(str(db_p))
        runs_rows_after = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        v2_rows = conn.execute(
            "SELECT COUNT(*) FROM v2_quality_results WHERE run_id = ?", (rid,)).fetchone()[0]
        conn.close()
        assert runs_rows_after == runs_rows_before
        assert v2_rows == 141


def test_save_is_idempotent_per_run():
    """Re-saving the same run replaces child rows rather than duplicating them."""
    for _, db_p in _temp_paths():
        store = V2QualityStore(db_path=db_p)
        rid = store.save_run(_run_v2_quality_corpus(), run_id="v2-repeat")
        # Second save of the same run_id must not duplicate evidence.
        store.save_run(_run_v2_quality_corpus(), run_id="v2-repeat")

        conn = sqlite3.connect(str(db_p))
        run_rows = conn.execute(
            "SELECT COUNT(*) FROM v2_quality_runs WHERE run_id = 'v2-repeat'").fetchone()[0]
        result_rows = conn.execute(
            "SELECT COUNT(*) FROM v2_quality_results WHERE run_id = 'v2-repeat'").fetchone()[0]
        conn.close()
        assert run_rows == 1
        assert result_rows == 141


def test_clear_run_removes_parent_and_children():
    for _, db_p in _temp_paths():
        store = V2QualityStore(db_path=db_p)
        rid = store.save_run(_run_v2_quality_corpus(), run_id="v2-temp")
        assert store.clear_run(rid) is True
        assert store.load_run(rid) is None
        assert _run_ids(db_p) == []


# ---------------------------------------------------------------------------
# 5. Act 11C-4B1: configuration-identity linkage (regression)
# ---------------------------------------------------------------------------


def _config(model_key, quant, loaded_context, with_batch=True):
    """Build a minimal Act-11B material-configuration dict.

    ``with_batch=False`` drops a required setting so classification becomes
    ``incomplete`` -- used to exercise distinct classification recovery too.
    """
    cfg = {
        "model_key": model_key,
        "model_quantization": quant,
        "loaded_context": loaded_context,
        "reasoning_mode": "off",
        "kv_cache_k_quantization": "unknown",
        "kv_cache_v_quantization": "unknown",
        "flash_attention": 0,
        "offload_kv_cache_to_gpu": 0,
        "cpu_model": "test-cpu",
        "installed_ram_bytes": 1,
    }
    if with_batch:
        cfg["eval_batch_size"] = 1
        cfg["physical_batch_size"] = 1
        cfg["parallel"] = 1
    return cfg


def _expected_row(run_id, seq, entry):
    """Mirror the exact granular-shape the store returns after a round trip."""
    return {
        "run_id": run_id,
        "seq": seq,
        "case_id": entry["case_id"],
        "category": entry["category"],
        "passed": bool(entry["passed"]),
        "checks_passed": entry["checks_passed"],
        "checks_total": entry["checks_total"],
        "failure_type": entry["failure_type"] or "",
        "failure_reason": entry["failure_reason"] or "",
        "expected": entry["expected"] or "",
        "actual": entry["actual"] or "",
    }


def test_two_configurations_remains_distinguishable_with_identical_scores():
    """Two V2 results with identical quality scores must stay distinguishable via
    their configuration identity, and every persisted evidence field survives."""
    for _, db_p in _temp_paths():
        # Two *different* real Act-11B configurations -> two different fingerprints.
        cfg_a = _config("qwen-test-A", "Q5_K_M", 20000)          # complete -> canonical
        cfg_b = _config("qwen-test-B", "Q5_K_M", 20000,
                        with_batch=False)                          # incomplete
        fp_a = compute_configuration_fingerprint(cfg_a)
        fp_b = compute_configuration_fingerprint(cfg_b)
        cls_a = classify_run_for_result(cfg_a)
        cls_b = classify_run_for_result(cfg_b)
        assert fp_a and fp_b and fp_a != fp_b, "precondition: fingerprints must differ"

        # The SAME corpus (identical 53 / 141 / 166) persisted under each identity.
        result = _run_v2_quality_corpus()
        store = V2QualityStore(db_path=db_p)
        rid_a = store.save_run(
            result, run_id="v2-ident-A",
            configuration_fingerprint=fp_a,
            model_identifier=cfg_a["model_key"],
            classification=cls_a,
        )
        rid_b = store.save_run(
            result, run_id="v2-ident-B",
            configuration_fingerprint=fp_b,
            model_identifier=cfg_b["model_key"],
            classification=cls_b,
        )

        # Both retain identical quality aggregates...
        for rid in (rid_a, rid_b):
            run = store.load_run(rid)["run"]
            assert run["logical_cases_attempted"] == 53
            assert run["result_entries"] == 141
            assert run["checks_passed"] == 166
            assert run["checks_total"] == 166

        # ...but are told apart by configuration identity (never by scores).
        ra = store.load_run(rid_a)["run"]
        rb = store.load_run(rid_b)["run"]
        assert ra["configuration_fingerprint"] == fp_a
        assert rb["configuration_fingerprint"] == fp_b
        assert ra["model_identifier"] == "qwen-test-A"
        assert rb["model_identifier"] == "qwen-test-B"
        assert ra["classification"] == cls_a
        assert rb["classification"] == cls_b
        assert (ra["configuration_fingerprint"], ra["model_identifier"]) != (
            rb["configuration_fingerprint"], rb["model_identifier"],
        )

        # Round-trip granular evidence unchanged (incl. seq ordering + run_id link).
        exp_a = [_expected_row(rid_a, i, e) for i, e in enumerate(result["results"])]
        exp_b = [_expected_row(rid_b, i, e) for i, e in enumerate(result["results"])]
        assert store.load_run(rid_a)["results"] == exp_a
        assert store.load_run(rid_b)["results"] == exp_b

        # Child schema is unchanged: v2_quality_results keeps exactly its 12 columns
        # (no identity fields duplicated onto granular rows).
        conn = sqlite3.connect(str(db_p))
        child_cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(v2_quality_results)")}
        conn.close()
        assert child_cols == {
            "id", "run_id", "seq", "case_id", "category", "passed",
            "checks_passed", "checks_total", "failure_type",
            "failure_reason", "expected", "actual",
        }


def test_missing_identity_is_blank_and_still_persistable():
    """A V2 result persisted without configuration-identity fields still round-trips.
    (Backwards compatibility for the opaque-run_id / pre-linkage behaviour.)"""
    for _, db_p in _temp_paths():
        store = V2QualityStore(db_path=db_p)
        rid = store.save_run(_run_v2_quality_corpus(), run_id="v2-no-identity")
        loaded = store.load_run(rid)["run"]
        assert loaded["logical_cases_attempted"] == 53
        assert loaded["configuration_fingerprint"] in (None, "")
        assert loaded["model_identifier"] in (None, "")
        # Evidence still fully present.
        assert len(store.load_run(rid)["results"]) == 141
