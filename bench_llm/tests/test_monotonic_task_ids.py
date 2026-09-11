"""Regression tests for monotonic task ID allocation.

Ensures that deleted tasks never have their IDs reused, and that
historical task-run references remain valid after deletions.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestMonotonicTaskIds(unittest.TestCase):
    """Tests for non-reusing task ID allocation."""

    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), "test_monotonic.db")
        import src.task_manager as tm
        tm.DB_PATH = self.db_path
        tm.init_tasks_table()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_create_then_delete_then_create_no_reuse(self):
        """Creating, deleting, and creating again must never reuse IDs."""
        import src.task_manager as tm

        t1 = tm.create_task(name="Alpha", task_type="markdown", prompt="p")
        t2 = tm.create_task(name="Beta", task_type="python", prompt="p")
        t3 = tm.create_task(name="Gamma", task_type="java", prompt="p")

        ids_before = [t["task_id"] for t in [t1, t2, t3]]

        # Delete the middle task
        tm.delete_task(t2["task_id"])

        # Create a new task — must NOT reuse t2's ID
        t4 = tm.create_task(name="Delta", task_type="unsolvable", prompt="p")
        self.assertNotIn(t4["task_id"], ids_before,
                         "New task ID must not reuse any previously used ID")

    def test_monotonic_increasing(self):
        """Task numbers must strictly increase across creations."""
        import src.task_manager as tm

        nums = []
        for i in range(5):
            t = tm.create_task(name=f"Task{i}", task_type="markdown", prompt="p")
            num = int(t["task_id"].rsplit("-", 1)[1])
            nums.append(num)

        for i in range(1, len(nums)):
            self.assertGreater(nums[i], nums[i - 1],
                               f"Task number {nums[i]} must be greater than {nums[i - 1]}")

    def test_historical_task_runs_preserved_after_delete(self):
        """Deleting a task must not break existing task_run references."""
        import src.task_manager as tm

        t1 = tm.create_task(name="Historical", task_type="markdown", prompt="p")
        run = tm.create_task_run(
            task_id=t1["task_id"],
            task_name="Historical",
            task_type="markdown",
            model="test-model",
            timestamp="2026-01-01T00:00:00Z",
            passed=True,
            score=1.0,
        )
        run_id = run["id"]

        # Delete the task
        tm.delete_task(t1["task_id"])

        # The task_run must still exist and reference the original ID
        runs = tm.get_task_runs()
        found = any(r["id"] == run_id for r in runs)
        self.assertTrue(found, "Task run must survive deletion of its parent task")

        # Verify the original task_id is still referenced correctly
        matching = [r for r in runs if r["id"] == run_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["task_id"], t1["task_id"])


if __name__ == "__main__":
    unittest.main()
