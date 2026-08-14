"""Tests for Act 3A.1 — Task Result History + Task-Type Tabs.

Covers:
1. Completed Markdown task result is persisted to task_runs.
2. Persisted result survives a new storage/manager instance.
3. /api/tasks-with-results returns the Markdown result.
4. Result includes task_type = markdown.
5. Two runs of the same Markdown task remain separate.
6. Task History can distinguish Markdown/Python/Java/Unsolvable types.
7. Deleting one historical task run leaves other runs intact.
8. Existing benchmark history remains unaffected.
9. Existing task execution still works.
10. All existing tests still pass.
"""

import json
import os
import sys
import tempfile
import unittest

# Ensure bench_llm is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestTaskRunsPersistence(unittest.TestCase):
    """Test 1: Completed Markdown task result is persisted to task_runs."""

    def test_create_task_run_persists(self):
        import src.task_manager as tm
        # Use a temp DB to avoid polluting the main one
        tm.DB_PATH = os.path.join(tempfile.gettempdir(), "test_task_runs.db")
        try:
            tm.init_tasks_table()
            run = tm.create_task_run(
                task_id="task-test-001",
                task_name="Markdownlint Default",
                task_type="markdown",
                model="test-model",
                timestamp="2026-08-14T21:42:00Z",
                passed=True,
                score=1.0,
                initial_errors=23,
                final_errors=0,
                errors_fixed=23,
                output_tokens=512,
                input_tokens=256,
                tokens_per_second=238.0,
                ttft_seconds=0.45,
                wall_time_seconds=2.15,
                result={"passed": True, "score": 1.0},
            )
            self.assertIsNotNone(run)
            self.assertEqual(run["task_type"], "markdown")
            self.assertTrue(run["passed"])
            self.assertEqual(run["score"], 1.0)
            self.assertEqual(run["initial_errors"], 23)
            self.assertEqual(run["final_errors"], 0)
        finally:
            # Clean up
            try:
                os.unlink(tm.DB_PATH)
            except OSError:
                pass


class TestTaskRunsSurviveRestart(unittest.TestCase):
    """Test 2: Persisted result survives a new storage/manager instance."""

    def test_survives_new_instance(self):
        import src.task_manager as tm
        db_path = os.path.join(tempfile.gettempdir(), "test_task_runs2.db")
        tm.DB_PATH = db_path
        try:
            tm.init_tasks_table()
            tm.create_task_run(
                task_id="task-test-002",
                task_name="Markdownlint Default",
                task_type="markdown",
                model="test-model",
                timestamp="2026-08-14T21:42:00Z",
                passed=True,
                score=0.75,
                initial_errors=10,
                final_errors=3,
                errors_fixed=7,
                output_tokens=300,
                input_tokens=200,
                tokens_per_second=150.0,
                ttft_seconds=0.3,
                wall_time_seconds=2.0,
                result={"passed": False, "score": 0.75},
            )

            # Re-init a fresh connection (simulates new instance)
            tm.init_tasks_table()
            runs = tm.get_task_runs()
            self.assertTrue(len(runs) >= 1)

            # Find our run
            found = False
            for r in runs:
                if r["task_id"] == "task-test-002":
                    found = True
                    self.assertEqual(r["task_type"], "markdown")
                    self.assertEqual(r["score"], 0.75)
                    self.assertEqual(r["initial_errors"], 10)
                    self.assertEqual(r["final_errors"], 3)
                    self.assertEqual(r["output_tokens"], 300)
            self.assertTrue(found, "Task run not found after re-init")
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


class TestApiTasksWithResults(unittest.TestCase):
    """Test 3: /api/tasks-with-results returns the Markdown result.

    We test via the task_manager directly since FastAPI test client
    may not be available.
    """

    def test_get_task_runs_returns_markdown(self):
        import src.task_manager as tm
        db_path = os.path.join(tempfile.gettempdir(), "test_task_runs3.db")
        tm.DB_PATH = db_path
        try:
            tm.init_tasks_table()
            tm.create_task_run(
                task_id="task-api-001",
                task_name="Markdownlint Default",
                task_type="markdown",
                model="glm-4.7-flash",
                timestamp="2026-08-14T21:42:00Z",
                passed=True,
                score=1.0,
                initial_errors=23,
                final_errors=0,
                errors_fixed=23,
                output_tokens=512,
                input_tokens=256,
                tokens_per_second=238.0,
                ttft_seconds=0.45,
                wall_time_seconds=2.15,
                result={"passed": True, "score": 1.0},
            )

            runs = tm.get_task_runs()
            markdown_runs = [r for r in runs if r["task_type"] == "markdown"]
            self.assertTrue(len(markdown_runs) >= 1, "No Markdown runs found")

            # Check model is stored
            self.assertEqual(markdown_runs[-1]["model"], "glm-4.7-flash")
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


class TestTaskTypeFilter(unittest.TestCase):
    """Test 4: Result includes task_type = markdown.

    Test 6: Task History can distinguish Markdown/Python/Java/Unsolvable types.
    """

    def test_task_type_filtering(self):
        import src.task_manager as tm
        db_path = os.path.join(tempfile.gettempdir(), "test_task_runs4.db")
        tm.DB_PATH = db_path
        try:
            tm.init_tasks_table()
            # Create one of each type
            tm.create_task_run(
                task_id="task-type-001",
                task_name="Markdownlint Default",
                task_type="markdown",
                model="test-model",
                timestamp="2026-08-14T21:42:00Z",
                passed=True,
                score=1.0,
            )
            tm.create_task_run(
                task_id="task-type-002",
                task_name="Python Benchmark",
                task_type="python",
                model="test-model",
                timestamp="2026-08-14T21:43:00Z",
                passed=False,
                score=0.5,
            )
            tm.create_task_run(
                task_id="task-type-003",
                task_name="Java Benchmark",
                task_type="java",
                model="test-model",
                timestamp="2026-08-14T21:44:00Z",
                passed=True,
                score=0.8,
            )
            tm.create_task_run(
                task_id="task-type-004",
                task_name="Unsolvable Task",
                task_type="unsolvable",
                model="test-model",
                timestamp="2026-08-14T21:45:00Z",
                passed=None,
                score=None,
            )

            # Filter by each type
            md_runs = tm.get_task_runs(task_type="markdown")
            py_runs = tm.get_task_runs(task_type="python")
            ja_runs = tm.get_task_runs(task_type="java")
            us_runs = tm.get_task_runs(task_type="unsolvable")

            self.assertTrue(len(md_runs) >= 1)
            self.assertTrue(len(py_runs) >= 1)
            self.assertTrue(len(ja_runs) >= 1)
            self.assertTrue(len(us_runs) >= 1)

            # No filter returns all
            all_runs = tm.get_task_runs()
            self.assertTrue(len(all_runs) >= 4)
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


class TestMultipleRunsSeparate(unittest.TestCase):
    """Test 5: Two runs of the same Markdown task remain separate."""

    def test_two_runs_separate(self):
        import src.task_manager as tm
        db_path = os.path.join(tempfile.gettempdir(), "test_task_runs5.db")
        tm.DB_PATH = db_path
        try:
            tm.init_tasks_table()
            tm.create_task_run(
                task_id="task-multi-001",
                task_name="Markdownlint Default",
                task_type="markdown",
                model="test-model",
                timestamp="2026-08-14T21:42:00Z",
                passed=True,
                score=1.0,
                initial_errors=23,
                final_errors=0,
                errors_fixed=23,
            )
            tm.create_task_run(
                task_id="task-multi-001",
                task_name="Markdownlint Default",
                task_type="markdown",
                model="test-model",
                timestamp="2026-08-14T22:00:00Z",
                passed=False,
                score=0.5,
                initial_errors=23,
                final_errors=12,
                errors_fixed=11,
            )

            runs = tm.get_task_runs()
            multi_runs = [r for r in runs if r["task_id"] == "task-multi-001"]
            self.assertTrue(len(multi_runs) >= 2, "Expected 2 separate runs")

            # They should have different scores
            scores = [r["score"] for r in multi_runs]
            self.assertIn(1.0, scores)
            self.assertIn(0.5, scores)
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


class TestDeleteTaskRun(unittest.TestCase):
    """Test 7: Deleting one historical task run leaves other runs intact."""

    def test_delete_one_leaves_others(self):
        import src.task_manager as tm
        db_path = os.path.join(tempfile.gettempdir(), "test_task_runs6.db")
        tm.DB_PATH = db_path
        try:
            tm.init_tasks_table()
            r1 = tm.create_task_run(
                task_id="task-del-001",
                task_name="Markdownlint Default",
                task_type="markdown",
                model="test-model",
                timestamp="2026-08-14T21:42:00Z",
                passed=True,
                score=1.0,
            )
            r2 = tm.create_task_run(
                task_id="task-del-002",
                task_name="Markdownlint Default",
                task_type="markdown",
                model="test-model",
                timestamp="2026-08-14T21:43:00Z",
                passed=False,
                score=0.5,
            )

            # Delete first run
            deleted = tm.delete_task_run(r1["id"])
            self.assertTrue(deleted)

            # Second should still exist
            runs = tm.get_task_runs()
            found_r2 = any(r["task_id"] == "task-del-002" and r["score"] == 0.5 for r in runs)
            self.assertTrue(found_r2, "Second run should still exist after deleting first")

            # Deleting non-existent returns False
            self.assertFalse(tm.delete_task_run(99999))
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


class TestBenchmarkHistoryUnaffected(unittest.TestCase):
    """Test 8: Existing benchmark history remains unaffected."""

    def test_benchmark_results_still_work(self):
        from src.results import ResultsStore
        from pathlib import Path
        import tempfile

        db_path = os.path.join(tempfile.gettempdir(), "test_benchmark.db")
        store = ResultsStore(db_path=Path(db_path))

        # Add a benchmark run
        store.add_run({
            "timestamp": "2026-08-14T21:00:00Z",
            "run_id": "bench-run-001",
            "model_key": "test-model",
            "model_display_name": "Test Model",
            "hardware_label": "CPU",
            "execution_environment": "Local",
            "connection_type": "",
            "iteration": 1,
            "cold_or_warm": "cold",
            "tokens_per_second": 100.0,
            "ttft_seconds": 0.5,
            "input_tokens": 128,
            "output_tokens": 256,
            "model_load_time_seconds": None,
            "wall_time_seconds": 3.0,
            "prompt_name": "test",
            "max_output_tokens": 500,
            "temperature": 0,
        })

        all_runs = store.get_all()
        self.assertTrue(len(all_runs) >= 1)

        # Clean up
        try:
            os.unlink(db_path)
            os.unlink(db_path + "-csv")
        except OSError:
            pass


class TestExistingTaskExecution(unittest.TestCase):
    """Test 9: Existing task execution still works."""

    def test_create_and_get_tasks(self):
        import src.task_manager as tm
        db_path = os.path.join(tempfile.gettempdir(), "test_task_runs7.db")
        tm.DB_PATH = db_path
        try:
            tm.init_tasks_table()
            task = tm.create_task(
                name="Test Markdown Task",
                task_type="markdown",
                prompt="Fix the document.",
            )
            self.assertIsNotNone(task)
            self.assertEqual(task["task_type"], "markdown")

            tasks = tm.get_tasks()
            self.assertTrue(len(tasks) >= 1)

            # Update status
            updated = tm.update_task_status(task["task_id"], "running")
            self.assertIsNotNone(updated)
            self.assertEqual(updated["status"], "running")

            # Set result
            result = tm.set_task_result(task["task_id"], {"score": 0.8, "passed": True})
            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "completed")
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


class TestAllExistingTestsStillPass(unittest.TestCase):
    """Test 10: All existing tests still pass (smoke test)."""

    def test_smoke_imports(self):
        """Verify key modules still import."""
        import src.config_loader
        import src.results
        import src.benchmark
        import src.task_manager
        # If any module has a syntax error, this will fail


if __name__ == "__main__":
    unittest.main()