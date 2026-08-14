"""Tests for SQLite result storage in ResultsStore.

Run with: python -m pytest tests/test_sqlite_storage.py -v
"""

import csv
import sqlite3
import tempfile
from pathlib import Path

import pytest

# Ensure the project root is on sys.path
_PROJECT_ROOT = Path(__file__).parent.parent
import sys
sys.path.insert(0, str(_PROJECT_ROOT))

from src.results import ResultsStore, CSV_HEADERS, _DEFAULT_CSV_PATH, _DEFAULT_DB_PATH


# ====================================================================
# Helper: create a temporary ResultsStore with isolated paths
# ====================================================================

def _temp_paths():
    """Return (temp_csv, temp_db) paths and clean them up after the test."""
    tmp_csv = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    # Close immediately; we only need the paths
    tmp_csv.close()
    tmp_db.close()
    yield Path(tmp_csv.name), Path(tmp_db.name)
    # Cleanup
    for p in (Path(tmp_csv.name), Path(tmp_db.name)):
        if p.exists():
            p.unlink()


def _create_csv_with_rows(csv_path: Path, rows: list[list], header: list[str] = CSV_HEADERS) -> None:
    """Write rows to a CSV file for migration testing."""
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


# ====================================================================
# Test 1: Database creation
# ====================================================================

def test_database_is_created():
    """ResultsStore creates the SQLite database file on initialization."""
    for csv_p, db_p in _temp_paths():
        store = ResultsStore(csv_path=csv_p, db_path=db_p)
        assert db_p.exists(), "SQLite database file should be created"
        # Verify it's a valid database
        conn = sqlite3.connect(str(db_p))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t["name"] if isinstance(t, dict) else t[0] for t in tables}
        assert "runs" in table_names
        assert "metadata" in table_names
        conn.close()


def test_database_schema_runs_table():
    """The runs table has the expected columns."""
    for csv_p, db_p in _temp_paths():
        store = ResultsStore(csv_path=csv_p, db_path=db_p)
        conn = sqlite3.connect(str(db_p))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("PRAGMA table_info(runs)")
        columns = {row["name"] for row in cursor.fetchall()}
        conn.close()
        for col in CSV_HEADERS:
            assert col in columns, f"Missing column in runs table: {col}"
        assert "id" in columns


# ====================================================================
# Test 2: Saving a result
# ====================================================================

def test_save_result_to_sqlite():
    """Adding a run persists it to SQLite."""
    for csv_p, db_p in _temp_paths():
        store = ResultsStore(csv_path=csv_p, db_path=db_p)
        sample_run = {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "run_id": "save-test-001",
            "model_key": "test-model",
            "model_display_name": "Test Model",
            "hardware_label": "Test HW",
            "execution_environment": "Local",
            "connection_type": "",
            "iteration": 1,
            "cold_or_warm": "cold",
            "tokens_per_second": 42.5,
            "ttft_seconds": 1.23,
            "input_tokens": 100,
            "output_tokens": 200,
            "model_load_time_seconds": None,
            "wall_time_seconds": 5.67,
            "prompt_name": "Custom",
            "max_output_tokens": 500,
            "temperature": 0.0,
        }
        store.add_run(sample_run)

        # Verify SQLite directly
        conn = sqlite3.connect(str(db_p))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM runs")
        rows = cursor.fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["run_id"] == "save-test-001"
        assert rows[0]["tokens_per_second"] == 42.5


def test_save_result_in_memory():
    """Adding a run updates the in-memory list."""
    for csv_p, db_p in _temp_paths():
        store = ResultsStore(csv_path=csv_p, db_path=db_p)
        sample_run = {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "run_id": "mem-test-001",
            "model_key": "test-model",
            "model_display_name": "Test Model",
            "hardware_label": "Test HW",
            "execution_environment": "Local",
            "connection_type": "",
            "iteration": 1,
            "cold_or_warm": "cold",
            "tokens_per_second": 50.0,
            "ttft_seconds": 1.0,
            "input_tokens": 100,
            "output_tokens": 200,
            "model_load_time_seconds": None,
            "wall_time_seconds": 5.0,
            "prompt_name": "Custom",
            "max_output_tokens": 500,
            "temperature": 0.0,
        }
        store.add_run(sample_run)
        all_runs = store.get_all()
        assert len(all_runs) == 1
        assert all_runs[0]["run_id"] == "mem-test-001"


# ====================================================================
# Test 3: Loading saved results after reopening the store
# ====================================================================

def test_reload_results_from_sqlite():
    """A new ResultsStore instance loads data from SQLite."""
    for csv_p, db_p in _temp_paths():
        # First store: write data (this also sets migration flag)
        store1 = ResultsStore(csv_path=csv_p, db_path=db_p)
        sample_run = {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "run_id": "reload-test-001",
            "model_key": "reload-model",
            "model_display_name": "Reload Model",
            "hardware_label": "HW",
            "execution_environment": "Local",
            "connection_type": "",
            "iteration": 1,
            "cold_or_warm": "cold",
            "tokens_per_second": 100.0,
            "ttft_seconds": 0.5,
            "input_tokens": 50,
            "output_tokens": 100,
            "model_load_time_seconds": None,
            "wall_time_seconds": 3.0,
            "prompt_name": "Custom",
            "max_output_tokens": 500,
            "temperature": 0.0,
        }
        store1.add_run(sample_run)

        # Second store: reload — migration flag should prevent re-import.
        store2 = ResultsStore(csv_path=csv_p, db_path=db_p)
        all_runs = store2.get_all()
        assert len(all_runs) == 1
        assert all_runs[0]["run_id"] == "reload-test-001"
        assert all_runs[0]["model_key"] == "reload-model"
        assert all_runs[0]["tokens_per_second"] == 100.0


# ====================================================================
# Test 4: Two identical new runs create two separate records
# ====================================================================

def test_two_identical_runs_are_separate():
    """Running the same benchmark twice stores two separate rows."""
    for csv_p, db_p in _temp_paths():
        store = ResultsStore(csv_path=csv_p, db_path=db_p)
        identical_run = {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "run_id": "dup-run-001",
            "model_key": "test-model",
            "model_display_name": "Test Model",
            "hardware_label": "HW",
            "execution_environment": "Local",
            "connection_type": "",
            "iteration": 1,
            "cold_or_warm": "cold",
            "tokens_per_second": 50.0,
            "ttft_seconds": 1.0,
            "input_tokens": 100,
            "output_tokens": 200,
            "model_load_time_seconds": None,
            "wall_time_seconds": 5.0,
            "prompt_name": "Custom",
            "max_output_tokens": 500,
            "temperature": 0.0,
        }
        store.add_run(identical_run)
        store.add_run(identical_run)

        all_runs = store.get_all()
        assert len(all_runs) == 2
        # Both should have the same run_id
        assert all(r["run_id"] == "dup-run-001" for r in all_runs)

        # Verify in SQLite directly
        conn = sqlite3.connect(str(db_p))
        count = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE run_id = ?",
            ("dup-run-001",),
        ).fetchone()[0]
        conn.close()
        assert count == 2


# ====================================================================
# Test 5: Existing CSV migration works
# ====================================================================

def test_csv_migration_imports_rows():
    """Pre-existing CSV rows are imported into SQLite on first open."""
    for csv_p, db_p in _temp_paths():
        # Create a CSV with known data (no DB yet)
        _create_csv_with_rows(
            csv_p,
            [
                [
                    "2026-01-01T00:00:00+00:00", "migration-run-001",
                    "model-a", "Model A", "HW", "Local", "",
                    1, "cold", "45.0", "0.5", 50, 100, "", 3.0,
                    "MigrationTest", 500, 0,
                ],
                [
                    "2026-01-01T00:01:00+00:00", "migration-run-002",
                    "model-b", "Model B", "HW", "Local", "",
                    1, "cold", "55.0", "0.6", 60, 120, "", 4.0,
                    "MigrationTest", 500, 0,
                ],
            ],
        )

        # Open store for the first time — should trigger migration
        store = ResultsStore(csv_path=csv_p, db_path=db_p)

        all_runs = store.get_all()
        assert len(all_runs) == 2
        assert all_runs[0]["run_id"] == "migration-run-001"
        assert all_runs[1]["run_id"] == "migration-run-002"

        # Verify in SQLite directly
        conn = sqlite3.connect(str(db_p))
        count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        conn.close()
        assert count == 2


# ====================================================================
# Test 6: Migration can run twice without duplicating rows
# ====================================================================

def test_migration_is_idempotent():
    """Running migration twice does not duplicate imported CSV rows."""
    for csv_p, db_p in _temp_paths():
        # Create a CSV with 2 rows
        _create_csv_with_rows(
            csv_p,
            [
                [
                    "2026-01-01T00:00:00+00:00", "idem-run-001",
                    "model-a", "Model A", "HW", "Local", "",
                    1, "cold", "45.0", "0.5", 50, 100, "", 3.0,
                    "Custom", 500, 0,
                ],
                [
                    "2026-01-01T00:01:00+00:00", "idem-run-002",
                    "model-b", "Model B", "HW", "Local", "",
                    2, "warm", "50.0", "0.4", 50, 100, "", 2.5,
                    "Custom", 500, 0,
                ],
            ],
        )

        # First open — migration happens
        store1 = ResultsStore(csv_path=csv_p, db_path=db_p)
        count_after_first = len(store1.get_all())
        assert count_after_first == 2

        # Second open — migration should detect already-migrated CSV
        store2 = ResultsStore(csv_path=csv_p, db_path=db_p)
        count_after_second = len(store2.get_all())
        assert count_after_second == 2  # No duplication

        # Verify directly in SQLite
        conn = sqlite3.connect(str(db_p))
        total = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        conn.close()
        assert total == 2


# ====================================================================
# Test 7: CSV file is preserved after migration
# ====================================================================

def test_csv_preserved_after_migration():
    """The original CSV file is not deleted after migration."""
    for csv_p, db_p in _temp_paths():
        _create_csv_with_rows(
            csv_p,
            [
                [
                    "2026-01-01T00:00:00+00:00", "preserve-run-001",
                    "model-a", "Model A", "HW", "Local", "",
                    1, "cold", "45.0", "0.5", 50, 100, "", 3.0,
                    "Custom", 500, 0,
                ],
            ],
        )
        assert csv_p.exists()

        store = ResultsStore(csv_path=csv_p, db_path=db_p)
        assert csv_p.exists(), "CSV file should still exist after migration"

        # CSV should still have the original data
        with open(csv_p, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["run_id"] == "preserve-run-001"


# ====================================================================
# Test 8: Clear works with SQLite
# ====================================================================

def test_clear_clears_sqlite_and_csv():
    """clear() empties both in-memory and on-disk storage."""
    for csv_p, db_p in _temp_paths():
        store = ResultsStore(csv_path=csv_p, db_path=db_p)
        store.add_run({
            "timestamp": "2026-01-01T00:00:00+00:00",
            "run_id": "clear-test-001",
            "model_key": "model-a",
            "model_display_name": "Model A",
            "hardware_label": "HW",
            "execution_environment": "Local",
            "connection_type": "",
            "iteration": 1,
            "cold_or_warm": "cold",
            "tokens_per_second": 45.0,
            "ttft_seconds": 0.5,
            "input_tokens": 50,
            "output_tokens": 100,
            "model_load_time_seconds": None,
            "wall_time_seconds": 3.0,
            "prompt_name": "Custom",
            "max_output_tokens": 500,
            "temperature": 0,
        })
        store.clear()

        assert len(store.get_all()) == 0

        # Verify SQLite is empty
        conn = sqlite3.connect(str(db_p))
        count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        conn.close()
        assert count == 0


# ====================================================================
# Test 9: Empty CSV does not cause migration errors
# ====================================================================

def test_empty_csv_no_migration_error():
    """A CSV with only headers (no data rows) does not cause errors."""
    for csv_p, db_p in _temp_paths():
        # Create a CSV with only headers
        with open(csv_p, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)

        store = ResultsStore(csv_path=csv_p, db_path=db_p)
        assert len(store.get_all()) == 0


# ====================================================================
# Test 10: Metadata fingerprint is stored correctly
# ====================================================================

def test_migration_fingerprint_stored():
    """After migration, the metadata table records the migration flag."""
    for csv_p, db_p in _temp_paths():
        _create_csv_with_rows(
            csv_p,
            [
                [
                    "2026-01-01T00:00:00+00:00", "fp-run-001",
                    "model-a", "Model A", "HW", "Local", "",
                    1, "cold", "45.0", "0.5", 50, 100, "", 3.0,
                    "Custom", 500, 0,
                ],
                [
                    "2026-01-01T00:01:00+00:00", "fp-run-002",
                    "model-b", "Model B", "HW", "Local", "",
                    2, "warm", "50.0", "0.4", 50, 100, "", 2.5,
                    "Custom", 500, 0,
                ],
                [
                    "2026-01-01T00:02:00+00:00", "fp-run-003",
                    "model-c", "Model C", "HW", "Local", "",
                    3, "warm", "52.0", "0.3", 50, 100, "", 2.0,
                    "Custom", 500, 0,
                ],
            ],
        )

        store = ResultsStore(csv_path=csv_p, db_path=db_p)

        conn = sqlite3.connect(str(db_p))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT value FROM metadata WHERE key = ?",
            ("csv_migrated",),
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row["value"] == "1"
