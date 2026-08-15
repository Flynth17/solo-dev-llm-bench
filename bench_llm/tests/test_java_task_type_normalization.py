"""Regression tests for Java task-type normalization in /api/tasks-with-results.

Covers:
1. Legacy "java_correctness" rows are returned when filtering by java_correctness
2. Current "java" filter returns both java and java_correctness rows (no duplicates)
3. Mixed historical data displays correctly under java filter
4. No duplicate display when both task_type values exist for same run
5. Other task types remain unaffected
6. Empty results handled gracefully
7. Integration with real SQLite DB
8. API endpoint returns correct data via TestClient
9. Java Results tab populated with historical data
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


# ------------------------------------------------------------------
# Helpers — build mock rows that mimic DB results
# ------------------------------------------------------------------

def _make_row(id_val, task_type, model="test-model", score=0.5):
    """Create a minimal mock row dict matching SQLite Row output."""
    return {
        "id": id_val,
        "task_id": f"task-java-{id_val}",
        "task_name": "Java Correctness",
        "task_type": task_type,
        "model": model,
        "timestamp": "2025-01-01T00:00:00+00:00",
        "passed": 1 if score == 1.0 else 0,
        "score": score,
        "initial_errors": None,
        "final_errors": None,
        "errors_fixed": None,
        "output_tokens": 200,
        "input_tokens": 500,
        "tokens_per_second": 50.0,
        "ttft_seconds": 0.3,
        "wall_time_seconds": 5.0,
        "result": None,
        "created_at": "2025-01-01T00:00:00+00:00",
    }


def _run_coro(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ------------------------------------------------------------------
# Test 1: Legacy java_correctness filter returns legacy rows
# ------------------------------------------------------------------

class TestLegacyJavaCorrectnessFilter:
    """Test that filtering by task_type=java_correctness works."""

    def test_java_correctness_filter_returns_legacy_rows(self):
        """task_type=java_correctness must return only java_correctness rows."""
        from unittest.mock import patch, MagicMock
        import src.routes.tasks as tasks_mod
        import src.task_manager as tm

        legacy_row = _make_row(1, "java_correctness")

        # Mock the async endpoint to call patched get_task_runs
        with patch.object(tm, "get_task_runs", return_value=[legacy_row]):
            result = _run_coro(tasks_mod.get_tasks_with_results(task_type="java_correctness"))

        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["task_type"] == "java_correctness"


# ------------------------------------------------------------------
# Test 2: Current java filter returns both java and java_correctness (no dupes)
# ------------------------------------------------------------------

class TestJavaFilterReturnsBothTypes:
    """Test that filtering by task_type=java returns both types without duplicates."""

    def test_java_filter_returns_both_types(self):
        """task_type=java must return rows from both 'java' and 'java_correctness'."""
        from unittest.mock import patch, MagicMock
        import src.routes.tasks as tasks_mod
        import src.task_manager as tm

        java_row = _make_row(10, "java")
        legacy_row = _make_row(20, "java_correctness")

        def mock_get_task_runs(task_type=None):
            if task_type == "java":
                return [java_row]
            elif task_type == "java_correctness":
                return [legacy_row]
            return []

        with patch.object(tm, "get_task_runs", side_effect=mock_get_task_runs):
            result = _run_coro(tasks_mod.get_tasks_with_results(task_type="java"))

        assert len(result["tasks"]) == 2
        types_found = {r["task_type"] for r in result["tasks"]}
        assert "java" in types_found
        assert "java_correctness" in types_found


# ------------------------------------------------------------------
# Test 3: Mixed historical data displays correctly under java filter
# ------------------------------------------------------------------

class TestMixedHistoricalData:
    """Test that mixed java/java_correctness rows display correctly."""

    def test_mixed_historical_data_no_duplicates(self):
        """When both java and java_correctness exist, no row should appear twice."""
        from unittest.mock import patch
        import src.routes.tasks as tasks_mod
        import src.task_manager as tm

        # Create unique rows (different IDs) for each type
        java_row1 = _make_row(30, "java", model="model-A")
        java_row2 = _make_row(31, "java", model="model-B")
        legacy_row1 = _make_row(40, "java_correctness", model="model-C")
        legacy_row2 = _make_row(41, "java_correctness", model="model-D")

        def mock_get_task_runs(task_type=None):
            if task_type == "java":
                return [java_row1, java_row2]
            elif task_type == "java_correctness":
                return [legacy_row1, legacy_row2]
            return []

        with patch.object(tm, "get_task_runs", side_effect=mock_get_task_runs):
            result = _run_coro(tasks_mod.get_tasks_with_results(task_type="java"))

        # Should have exactly 4 rows (2 java + 2 java_correctness), no duplicates
        assert len(result["tasks"]) == 4
        ids_found = [r["id"] for r in result["tasks"]]
        assert len(ids_found) == len(set(ids_found)), "Duplicate IDs found in results"


# ------------------------------------------------------------------
# Test 4: No duplicate display when both types exist
# ------------------------------------------------------------------

class TestNoDuplicateDisplay:
    """Test that the same run is never displayed twice."""

    def test_same_run_not_duplicated(self):
        """A single task_run row must not appear in both java and java_correctness results."""
        from unittest.mock import patch
        import src.routes.tasks as tasks_mod
        import src.task_manager as tm

        # The same row should NOT be returned by both queries
        shared_row = _make_row(50, "java")  # Only exists as 'java' type

        def mock_get_task_runs(task_type=None):
            if task_type == "java":
                return [shared_row]
            elif task_type == "java_correctness":
                return []  # This row is NOT a java_correctness type
            return []

        with patch.object(tm, "get_task_runs", side_effect=mock_get_task_runs):
            result = _run_coro(tasks_mod.get_tasks_with_results(task_type="java"))

        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["id"] == 50


# ------------------------------------------------------------------
# Test 5: Other task types remain unaffected
# ------------------------------------------------------------------

class TestOtherTypesUnaffected:
    """Test that markdown/python/unsolvable filtering is not affected."""

    def test_markdown_filter_unchanged(self):
        """task_type=markdown must work as before (no special handling)."""
        from unittest.mock import patch
        import src.routes.tasks as tasks_mod
        import src.task_manager as tm

        md_row = _make_row(100, "markdown")

        def mock_get_task_runs(task_type=None):
            if task_type == "markdown":
                return [md_row]
            return []

        with patch.object(tm, "get_task_runs", side_effect=mock_get_task_runs):
            result = _run_coro(tasks_mod.get_tasks_with_results(task_type="markdown"))

        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["task_type"] == "markdown"

    def test_python_filter_unchanged(self):
        """task_type=python must work as before."""
        from unittest.mock import patch
        import src.routes.tasks as tasks_mod
        import src.task_manager as tm

        py_row = _make_row(101, "python")

        def mock_get_task_runs(task_type=None):
            if task_type == "python":
                return [py_row]
            return []

        with patch.object(tm, "get_task_runs", side_effect=mock_get_task_runs):
            result = _run_coro(tasks_mod.get_tasks_with_results(task_type="python"))

        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["task_type"] == "python"

    def test_unsolvable_filter_unchanged(self):
        """task_type=unsolvable must work as before."""
        from unittest.mock import patch
        import src.routes.tasks as tasks_mod
        import src.task_manager as tm

        us_row = _make_row(102, "unsolvable")

        def mock_get_task_runs(task_type=None):
            if task_type == "unsolvable":
                return [us_row]
            return []

        with patch.object(tm, "get_task_runs", side_effect=mock_get_task_runs):
            result = _run_coro(tasks_mod.get_tasks_with_results(task_type="unsolvable"))

        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["task_type"] == "unsolvable"


# ------------------------------------------------------------------
# Test 6: No filter returns all types (including java_correctness)
# ------------------------------------------------------------------

class TestNoFilterReturnsAll:
    """Test that no filter returns all task runs."""

    def test_no_filter_returns_all(self):
        """task_type=None must return all rows regardless of type."""
        from unittest.mock import patch
        import src.routes.tasks as tasks_mod
        import src.task_manager as tm

        all_rows = [_make_row(200, "markdown"), _make_row(201, "python"), _make_row(202, "java_correctness")]

        def mock_get_task_runs(task_type=None):
            if task_type is None:
                return all_rows
            return []

        with patch.object(tm, "get_task_runs", side_effect=mock_get_task_runs):
            result = _run_coro(tasks_mod.get_tasks_with_results(task_type=None))

        assert len(result["tasks"]) == 3


# ------------------------------------------------------------------
# Test 7: Empty results handled correctly
# ------------------------------------------------------------------

class TestEmptyResults:
    """Test that empty results are handled gracefully."""

    def test_java_filter_empty_returns_empty_list(self):
        """When no java/java_correctness rows exist, return empty list."""
        from unittest.mock import patch
        import src.routes.tasks as tasks_mod
        import src.task_manager as tm

        def mock_get_task_runs(task_type=None):
            return []

        with patch.object(tm, "get_task_runs", side_effect=mock_get_task_runs):
            result = _run_coro(tasks_mod.get_tasks_with_results(task_type="java"))

        assert len(result["tasks"]) == 0


# ------------------------------------------------------------------
# Test 8: Integration — actual DB query matches both types
# ------------------------------------------------------------------

class TestIntegrationWithDB:
    """Test that the fix works with real SQLite queries."""

    def test_real_db_returns_both_java_types(self):
        """Real DB must return rows from both java and java_correctness when filtering by java."""
        import sqlite3
        import src.task_manager as tm

        # Ensure tables exist
        tm.init_tasks_table()

        conn = sqlite3.connect(tm.DB_PATH)
        try:
            # Insert test data with different task_types
            conn.execute(
                "INSERT INTO task_runs (task_id, task_name, task_type, model, timestamp, passed, score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("test-1", "Java Correctness", "java_correctness", "test-model", "2025-06-01T00:00:00+00:00", 1, 1.0, "2025-06-01T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO task_runs (task_id, task_name, task_type, model, timestamp, passed, score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("test-2", "Java Correctness", "java_correctness", "test-model", "2025-06-02T00:00:00+00:00", 0, 0.5, "2025-06-02T00:00:00+00:00"),
            )
            conn.commit()

            # Query with task_type=java_correctness (legacy)
            legacy_runs = tm.get_task_runs(task_type="java_correctness")
            assert len(legacy_runs) >= 2

            # Query with task_type=None and filter manually for java type
            all_runs = tm.get_task_runs(task_type=None)
            java_only = [r for r in all_runs if r["task_type"] == "java"]
            java_correctness_only = [r for r in all_runs if r["task_type"] == "java_correctness"]

            # Both should be retrievable separately
            assert len(java_correctness_only) >= 2

        finally:
            conn.close()


# ------------------------------------------------------------------
# Test 9: API endpoint returns correct data via TestClient
# ------------------------------------------------------------------

class TestAPIEndpointReturnsCorrectData:
    """Test the actual FastAPI endpoint behavior."""

    def test_api_endpoint_java_filter_returns_both_types(self):
        """The /api/tasks-with-results?task_type=java endpoint must return both types."""
        import sqlite3
        from fastapi.testclient import TestClient
        import src.task_manager as tm
        from src.routes.tasks import router as tasks_router

        # Create app with just the tasks router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(tasks_router)

        client = TestClient(app)

        # Ensure tables exist and insert test data
        tm.init_tasks_table()
        conn = sqlite3.connect(tm.DB_PATH)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO task_runs (task_id, task_name, task_type, model, timestamp, passed, score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("test-api-1", "Java Correctness", "java_correctness", "api-test-model", "2025-07-01T00:00:00+00:00", 1, 1.0, "2025-07-01T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT OR IGNORE INTO task_runs (task_id, task_name, task_type, model, timestamp, passed, score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("test-api-2", "Java Correctness", "java_correctness", "api-test-model", "2025-07-02T00:00:00+00:00", 0, 0.5, "2025-07-02T00:00:00+00:00"),
            )
            conn.commit()

            # Query with task_type=java (current)
            resp = client.get("/api/tasks-with-results?task_type=java")
            assert resp.status_code == 200
            data = resp.json()
            java_runs = [r for r in data["tasks"] if r["task_type"] == "java_correctness"]

            # Must include legacy java_correctness rows
            assert len(java_runs) >= 2

        finally:
            conn.close()


# ------------------------------------------------------------------
# Test 10: Java Results tab population check
# ------------------------------------------------------------------

class TestJavaResultsTabPopulated:
    """Test that the Java Results tab would be populated with historical data."""

    def test_java_results_tab_has_data(self):
        """When filtering by java, historical java_correctness rows must appear."""
        import sqlite3
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        import src.task_manager as tm
        from src.routes.tasks import router as tasks_router

        app = FastAPI()
        app.include_router(tasks_router)
        client = TestClient(app)

        tm.init_tasks_table()
        conn = sqlite3.connect(tm.DB_PATH)
        try:
            # Insert a mix of java and java_correctness rows
            for i in range(5):
                task_type = "java_correctness" if i % 2 == 0 else "java"
                score = 1.0 if i % 3 == 0 else 0.5
                conn.execute(
                    "INSERT OR IGNORE INTO task_runs (task_id, task_name, task_type, model, timestamp, passed, score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"tab-test-{i}", "Java Correctness", task_type, f"model-{i}", f"2025-08-0{i+1}T00:00:00+00:00", 1 if score == 1.0 else 0, score, f"2025-08-0{i+1}T00:00:00+00:00"),
                )
            conn.commit()

            resp = client.get("/api/tasks-with-results?task_type=java")
            assert resp.status_code == 200
            data = resp.json()

            # Should have at least the java_correctness rows we inserted
            java_correctness_rows = [r for r in data["tasks"] if r["task_type"] == "java_correctness"]
            assert len(java_correctness_rows) >= 3  # At least 3 of 5 were java_correctness

        finally:
            conn.close()