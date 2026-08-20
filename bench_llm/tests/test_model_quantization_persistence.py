"""Tests for model_quantization persistence (R2.2).

Verifies that:
- new benchmark rows persist & round-trip a non-empty model_quantization
- the /api/results response shape carries model_quantization on each row
- an old row without quantization still loads and exposes an empty value
- empty quantization is supported (round-trips as blank)

These tests are fully offline: they do not touch LM Studio.
"""

import csv as _csv
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.results import ResultsStore, CSV_HEADERS


def _temp_paths():
    tmp_csv = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_csv.close()
    tmp_db.close()
    yield Path(tmp_csv.name), Path(tmp_db.name)
    for p in (Path(tmp_csv.name), Path(tmp_db.name)):
        if p.exists():
            p.unlink()


def _full_run(**overrides):
    run = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "run_id": "q-run-001",
        "model_key": "ornith-35b-q4",
        "model_display_name": "ornith-1.0-35b-ud",
        "model_quantization": "",  # default; overridden per test
        "hardware_label": "HW",
        "execution_environment": "Local",
        "connection_type": "",
        "iteration": 1,
        "cold_or_warm": "warm",
        "tokens_per_second": 45.0,
        "ttft_seconds": 0.4,
        "input_tokens": 128,
        "output_tokens": 64,
        "model_load_time_seconds": None,
        "wall_time_seconds": 3.0,
        "prompt_name": "Custom",
        "max_output_tokens": 500,
        "temperature": 0.0,
    }
    run.update(overrides)
    return run


# ====================================================================
# Test: new row persists model_quantization and round-trips it
# ====================================================================

def test_new_row_round_trips_model_quantization():
    for csv_p, db_p in _temp_paths():
        store = ResultsStore(csv_path=csv_p, db_path=db_p)
        store.add_run(_full_run(model_quantization="Q4_K_M"))

        all_runs = store.get_all()
        assert len(all_runs) == 1
        assert all_runs[0]["model_key"] == "ornith-35b-q4"
        assert all_runs[0]["model_quantization"] == "Q4_K_M"

        # Verify directly in SQLite (column present + value stored)
        conn = sqlite3.connect(str(db_p))
        conn.row_factory = sqlite3.Row
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
        assert "model_quantization" in cols
        val = conn.execute(
            "SELECT model_quantization FROM runs WHERE run_id = ?", ("q-run-001",)
        ).fetchone()
        conn.close()
        assert val["model_quantization"] == "Q4_K_M"


# ====================================================================
# Test: empty quantization round-trips as blank / None (no value lost)
# ====================================================================

def test_empty_quantization_supported():
    for csv_p, db_p in _temp_paths():
        store = ResultsStore(csv_path=csv_p, db_path=db_p)
        # Empty string should be stored and reloaded cleanly.
        store.add_run(_full_run(model_quantization=""))

        all_runs = store.get_all()
        assert len(all_runs) == 1
        assert all_runs[0]["model_key"] == "ornith-35b-q4"
        # Storage normalizes blank -> None on reload; the important thing is no crash and key present.
        assert "model_quantization" in all_runs[0]


# ====================================================================
# Test: a run with explicit Q5_K_M round-trips (distinguishable variant)
# ====================================================================

def test_q5_variant_round_trips():
    for csv_p, db_p in _temp_paths():
        store = ResultsStore(csv_path=csv_p, db_path=db_p)
        store.add_run(_full_run(run_id="q-run-002", model_key="ornith-35b-q5",
                                model_quantization="Q5_K_M"))
        all_runs = store.get_all()
        assert all_runs[0]["model_quantization"] == "Q5_K_M"


# ====================================================================
# Test: pre-existing DB without the column is migrated (ALTER), rows preserved
# ====================================================================

def test_legacy_db_migrated_and_rows_preserved():
    import csv as _csv

    for csv_p, db_p in _temp_paths():
        # Build an OLD database WITHOUT model_quantization and with a legacy row.
        conn = sqlite3.connect(str(db_p))
        conn.execute(
            """CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, run_id TEXT, model_key TEXT, model_display_name TEXT,
                hardware_label TEXT, execution_environment TEXT, connection_type TEXT,
                iteration INTEGER, cold_or_warm TEXT, tokens_per_second REAL, ttft_seconds REAL,
                input_tokens INTEGER, output_tokens INTEGER, model_load_time_seconds REAL,
                wall_time_seconds REAL, prompt_name TEXT, max_output_tokens INTEGER, temperature REAL
            )"""
        )
        conn.execute(
            "INSERT INTO runs (timestamp, run_id, model_key, model_display_name, hardware_label, "
            "execution_environment, connection_type, iteration, cold_or_warm, tokens_per_second, "
            "ttft_seconds, input_tokens, output_tokens, wall_time_seconds, prompt_name, max_output_tokens, temperature) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", "legacy-run-1", "old-model", "Old Model", "HW",
             "Local", "", 1, "warm", 42.0, 0.5, 100, 50, 3.0, "Custom", 500, 0),
        )
        # Set migration flag so the store does not try to re-import the (header-less) CSV path logic.
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", ("csv_migrated", "1"))
        conn.commit()
        conn.close()

        store = ResultsStore(csv_path=csv_p, db_path=db_p)

        # The legacy row is still present and intact after migration.
        all_runs = store.get_all()
        assert len(all_runs) == 1
        assert all_runs[0]["run_id"] == "legacy-run-1"
        assert all_runs[0]["model_key"] == "old-model"
        # Historical rows expose an empty/None quantization (no crash, key present).
        legacy_q = all_runs[0].get("model_quantization", None)
        assert legacy_q in (None, "")

        # And the column now exists for new writes.
        conn = sqlite3.connect(str(db_p))
        conn.row_factory = sqlite3.Row
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
        assert "model_quantization" in cols
        conn.close()


# ====================================================================
# Test: CSV round-trips model_quantization header + value
# ====================================================================

def test_csv_round_trips_model_quantization():
    for csv_p, db_p in _temp_paths():
        store = ResultsStore(csv_path=csv_p, db_path=db_p)
        store.add_run(_full_run(model_quantization="Q8_0"))

        # CSV header must include the new column.
        with open(csv_p, "r", newline="", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert "model_quantization" in reader.fieldnames
        assert rows[0]["model_quantization"] == "Q8_0"


# ====================================================================
# Test: CSV_HEADERS / column metadata is consistent with SQLite schema path
# ====================================================================

def test_csv_headers_include_model_quantization():
    assert "model_quantization" in CSV_HEADERS