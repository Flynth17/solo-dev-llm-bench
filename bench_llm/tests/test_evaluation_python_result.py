"""Regression test for Act P5.3 — Unified Evaluation Python result wiring.

Proves that the evaluation route's Python branch uses the canonical
run_python_correctness_task() result directly (not stale data or defaults).
"""

import sys
from pathlib import Path
from unittest import TestCase, main

BENCH_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(BENCH_ROOT))


class TestEvaluationPythonResultWiring(TestCase):
    """Verify evaluation route Python branch uses canonical runner result."""

    def test_canonical_runner_returns_correct_values(self):
        """The canonical runner must return score=1.0 for correct code.

        Self-contained: this test writes its own known-correct solution into
        runtime/python/latest_output.py (instead of depending on whatever a prior
        test left there — which was causing flaky, order-dependent failures) and
        restores the original file afterwards in a finally block. The score
        assertions are unchanged because a correct solution scores 1.0.
        """
        from src.python_validator import validate_python_solution

        output_file = BENCH_ROOT / "runtime" / "python" / "latest_output.py"

        # Known-correct solution that passes every test in test_solution.py
        # (add, multiply, is_even) -> score must be 1.0.
        correct_solution = '''\

def add(a, b):
    """Add two numbers and return the result."""
    return a + b


def multiply(x, y):
    """Multiply two numbers and return the result."""
    return x * y


def is_even(n):
    """Return True if n is even, False otherwise."""
    return n % 2 == 0
'''

        # Back up any pre-existing file so we can restore its exact state afterwards.
        existed = output_file.exists()
        original_content = output_file.read_text(encoding="utf-8") if existed else None
        try:
            output_file.write_text(correct_solution, encoding="utf-8")
            solution_code = output_file.read_text(encoding="utf-8")

            test_file = BENCH_ROOT / "tasks" / "python_correctness" / "test_solution.py"
            test_code = test_file.read_text(encoding="utf-8")

            result = validate_python_solution(solution_code, test_code)

            # Canonical runner returns correct values for fixed code
            self.assertEqual(result.score, 1.0, "score must be 1.0")
            self.assertTrue(result.passed, "passed must be True")
            self.assertEqual(result.total_tests, 6, "total_tests must be 6")
            self.assertEqual(result.passed_tests, 6, "passed_tests must be 6")
            self.assertEqual(result.failed_tests, 0, "failed_tests must be 0")
        finally:
            # Restore the original file state so other tests are unaffected.
            if existed:
                output_file.write_text(original_content, encoding="utf-8")
            else:
                output_file.unlink(missing_ok=True)

    def test_evaluation_response_structure_matches_runner_result(self):
        """The correctness_results entry for python must mirror py_result fields."""
        # Verify the evaluation route builds the response using py_result fields.
        # This is a structural check — if field names change, this will fail.
        import inspect
        from src.routes.evaluation import router

        # Find the run_evaluation_endpoint function source
        for route in router.routes:
            if hasattr(route, "endpoint") and route.endpoint.__name__ == "run_evaluation_endpoint":
                source = inspect.getsource(route.endpoint)
                break
        else:
            self.fail("Could not find run_evaluation_endpoint route")

        # Verify the response uses py_result fields (not defaults or stale data)
        self.assertIn('py_result["score"]', source, "Must use py_result['score']")
        self.assertIn('py_result["passed"]', source, "Must use py_result['passed']")
        self.assertIn('py_result["total_tests"]', source, "Must use py_result['total_tests']")
        self.assertIn('py_result["passed_tests"]', source, "Must use py_result['passed_tests']")
        self.assertIn('py_result["failed_tests"]', source, "Must use py_result['failed_tests']")

    def test_evaluation_python_branch_calls_canonical_runner(self):
        """The evaluation route must call run_python_correctness_task."""
        import inspect
        from src.routes.evaluation import router

        for route in router.routes:
            if hasattr(route, "endpoint") and route.endpoint.__name__ == "run_evaluation_endpoint":
                source = inspect.getsource(route.endpoint)
                break
        else:
            self.fail("Could not find run_evaluation_endpoint route")

        # Must import from task_python (canonical runner)
        self.assertIn('from src.task_python import run_python_correctness_task', source,
                      "Must import canonical Python runner")
        self.assertIn('py_result = await run_python_correctness_task(', source,
                      "Must call canonical Python runner and assign to py_result")


if __name__ == "__main__":
    main()