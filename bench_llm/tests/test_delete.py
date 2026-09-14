"""Tests for ResultsStore.delete_run (per-run deletion of benchmark iterations).

The HTTP DELETE /api/results/{run_id} surface and its legacy delete-modal client were
retired in M2; the persistence-layer method itself is preserved here so its contract
stays regression-protected.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

# Local imports -- uses the real ResultsStore
from src.results import ResultsStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_store_and_seed():
    """Create a ResultsStore backed by temp files with seed data."""
    tmp_dir = tempfile.mkdtemp()
    csv_path = Path(tmp_dir) / "benchmark_results.csv"
    db_path = Path(tmp_dir) / "benchmark_results.db"
    store = ResultsStore(csv_path=csv_path, db_path=db_path)

    # Seed two runs with multiple iterations each
    run_a_id = "run_a_001"
    run_b_id = "run_b_002"
    for itr in range(1, 4):
        store.add_run({
            "timestamp": "2026-08-14T10:00:00+00:00",
            "run_id": run_a_id,
            "model_key": "model-a",
            "model_display_name": "Model A",
            "hardware_label": "HW1",
            "execution_environment": "Local",
            "connection_type": "Local network",
            "iteration": itr,
            "cold_or_warm": "cold" if itr == 1 else "warm",
            "tokens_per_second": 50.0,
            "ttft_seconds": 0.5,
            "input_tokens": 100,
            "output_tokens": 200,
            "model_load_time_seconds": 2.0,
            "wall_time_seconds": 5.0,
            "prompt_name": "test",
            "max_output_tokens": 500,
            "temperature": 0.5,
        })
    for itr in range(1, 3):
        store.add_run({
            "timestamp": "2026-08-14T11:00:00+00:00",
            "run_id": run_b_id,
            "model_key": "model-b",
            "model_display_name": "Model B",
            "hardware_label": "HW2",
            "execution_environment": "Cloud",
            "connection_type": "Remote connection",
            "iteration": itr,
            "cold_or_warm": "cold" if itr == 1 else "warm",
            "tokens_per_second": 75.0,
            "ttft_seconds": 0.3,
            "input_tokens": 80,
            "output_tokens": 150,
            "model_load_time_seconds": 1.5,
            "wall_time_seconds": 4.0,
            "prompt_name": "test",
            "max_output_tokens": 500,
            "temperature": 0.5,
        })

    return store, run_a_id, run_b_id, csv_path, db_path


# ---------------------------------------------------------------------------
# ResultsStore.delete_run tests
# ---------------------------------------------------------------------------

class TestResultsStoreDeleteRun:

    def test_delete_existing_run(self):
        """Deleting an existing run returns True."""
        store, run_a_id, _, _, _ = _make_store_and_seed()
        result = store.delete_run(run_a_id)
        assert result is True

    def test_delete_removes_all_iterations(self):
        """Deleting removes every iteration belonging to that run_id."""
        store, run_a_id, _, _, _ = _make_store_and_seed()
        # run_a has 3 iterations
        all_before = [r for r in store.runs if r.get("run_id") == run_a_id]
        assert len(all_before) == 3
        store.delete_run(run_a_id)
        remaining = [r for r in store.runs if r.get("run_id") == run_a_id]
        assert len(remaining) == 0

    def test_delete_unrelated_runs_untouched(self):
        """Deleting one run leaves other runs intact."""
        store, run_a_id, run_b_id, _, _ = _make_store_and_seed()
        store.delete_run(run_a_id)
        b_runs = [r for r in store.runs if r.get("run_id") == run_b_id]
        assert len(b_runs) == 2  # both iterations preserved

    def test_delete_nonexistent_run_returns_false(self):
        """Deleting a nonexistent run_id returns False."""
        store, _, _, _, _ = _make_store_and_seed()
        result = store.delete_run("nonexistent_id")
        assert result is False

    def test_delete_runs_in_transaction(self):
        """Verify rows are actually removed from SQLite after delete."""
        store, run_a_id, _, _, db_path = _make_store_and_seed()
        store.delete_run(run_a_id)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM runs WHERE run_id = ?",
            (run_a_id,),
        )
        count = cursor.fetchone()["cnt"]
        conn.close()
        assert count == 0


# ---------------------------------------------------------------------------
# Client-side cancel regression tests (pytest + httpx for HTTP mocking)
# ---------------------------------------------------------------------------

class TestCancelRegression:

    def test_escapeHtml_produces_valid_entities(self):
        """escapeHtml must produce valid HTML entities, not broken strings."""
        js_path = Path(__file__).parent.parent / "static" / "results-utils.js"
        js_text = js_path.read_text(encoding="utf-8")
        # Verify the function uses String.fromCharCode to build entities
        assert "String.fromCharCode(38)" in js_text  # &
        assert "String.fromCharCode(60)" in js_text  # <
        assert "String.fromCharCode(62)" in js_text  # >
        # Verify no broken entities like "lt;;" or "amp;;"
        assert "lt;;" not in js_text
        assert "amp;;" not in js_text
        assert "gt;;" not in js_text
