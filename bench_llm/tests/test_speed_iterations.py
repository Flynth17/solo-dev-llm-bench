"""Regression tests for Speed Iterations in the unified Evaluation Suite (Act U5.4).

Proves:
- default Speed Iterations = 5 when omitted from the request body
- minimum allowed value = 1 (0 rejected, 1 accepted)
- one selected speed test with iterations=5 produces exactly 5 runs
  (iteration 1 cold, iterations 2-5 warm), all persisted as raw runs
- three selected speed tests with iterations=5 produce 15 persisted raw runs
- correctness runners are called ONCE regardless of speed iteration count
- run_benchmark classifies iteration 1 = cold and 2+ = warm and computes
  AVG / MIN / MAX from the real iteration set (warm aggregate excludes iter 1)

Does NOT modify correctness tests, Java history, or Raw Speed scoring.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from src.main import app
from src.benchmark import run_benchmark

client = TestClient(app)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _bench_result(iterations=5):
    """Shape of a successful run_benchmark() result."""
    runs = []
    for i in range(1, iterations + 1):
        runs.append({
            "iteration": i,
            "cold_or_warm": "cold" if i == 1 else "warm",
            "tokens_per_second": 100.0 + i,
            "ttft_seconds": round(0.02 / i, 4),
            "input_tokens": 100,
            "output_tokens": 500,
            "model_load_time_seconds": 3.0 if i == 1 else None,
            "wall_time_seconds": 5.0,
        })
    tps = [r["tokens_per_second"] for r in runs]
    return {
        "run_id": f"run-{iterations}",
        "timestamp": "2026-08-15T12:00:00+00:00",
        "model": "m",
        "hardware_label": "",
        "execution_environment": "Local",
        "connection_type": "",
        "prompt_name": "",
        "iterations": iterations,
        "runs": runs,
        "aggregate": {
            "avg_tokens_per_second": round(sum(tps) / len(tps), 2) if tps else 0.0,
            "min_tokens_per_second": min(tps) if tps else 0.0,
            "max_tokens_per_second": max(tps) if tps else 0.0,
        },
        "warm_aggregate": {"available": iterations > 1},
    }


def _mock_py_result():
    return {
        "task_name": "Python Correctness",
        "task_type": "python",
        "model": "m",
        "score": 0.5,
        "passed": False,
        "total_tests": 6,
        "passed_tests": 3,
        "failed_tests": 3,
        "output_tokens": 600,
        "input_tokens": 400,
        "tokens_per_second": 60.0,
        "ttft_seconds": None,
        "wall_time_seconds": 12.0,
    }


def _post_evaluation(payload):
    """Run /api/evaluation/run with all backend collaborators mocked.

    Returns (response, bench_mock, py_mock, create_run_mock, store_mock).
    """
    from contextlib import ExitStack

    with ExitStack() as stack:
        mock_bench = stack.enter_context(patch(
            "src.benchmark.run_benchmark", AsyncMock(return_value=_bench_result(payload.get("iterations", 5)))))
        mock_py = stack.enter_context(patch(
            "src.task_python.run_python_correctness_task", AsyncMock(return_value=_mock_py_result())))
        stack.enter_context(patch("src.task_manager.get_tasks", return_value=[
            {"task_id": "t-py", "name": "Python Correctness", "task_type": "python"},
        ]))
        mock_create_run = stack.enter_context(patch("src.task_manager.create_task_run"))
        store = stack.enter_context(patch("src.app_state.results_store"))

        resp = client.post("/api/evaluation/run", json=payload)

    return resp, mock_bench, mock_py, mock_create_run, store


# ------------------------------------------------------------------
# 1. Default Speed Iterations = 5
# ------------------------------------------------------------------

class TestDefaultSpeedIterations:
    def test_default_iterations_is_5(self):
        """Omitting 'iterations' from the request must fall back to 5."""
        resp, mock_bench, _mock_py, _run, _store = _post_evaluation({
            "model": "m",
            "speed_tests": ["small"],
            "correctness_tests": ["python"],
        })

        assert resp.status_code == 200
        mock_bench.assert_awaited_once()
        assert mock_bench.await_args.kwargs["iterations"] == 5


# ------------------------------------------------------------------
# 2. Minimum allowed value = 1
# ------------------------------------------------------------------

class TestMinIterationsAllowed:
    def test_zero_iterations_rejected(self):
        resp, mock_bench, _mock_py, _run, _store = _post_evaluation({
            "model": "m",
            "speed_tests": ["small"],
            "correctness_tests": ["python"],
            "iterations": 0,
        })

        assert resp.status_code == 400
        mock_bench.assert_not_awaited()

    def test_one_iteration_allowed(self):
        """iterations=1 must be accepted and produce exactly 1 run."""
        resp, mock_bench, _mock_py, _run, store = _post_evaluation({
            "model": "m",
            "speed_tests": ["small"],
            "correctness_tests": ["python"],
            "iterations": 1,
        })

        assert resp.status_code == 200
        assert mock_bench.await_args.kwargs["iterations"] == 1
        body = resp.json()
        assert len(body["speed_results"][0]["runs"]) == 1
        # Exactly one raw speed row persisted (correctness never enters Raw Speed)
        assert store.add_run.call_count == 1


# ------------------------------------------------------------------
# 3. One selected speed test, iterations=5 -> exactly 5 runs
# ------------------------------------------------------------------

class TestSingleSpeedTestFiveRuns:
    def test_small_x5_produces_5_runs(self):
        resp, mock_bench, _mock_py, _run, store = _post_evaluation({
            "model": "m",
            "speed_tests": ["small"],
            "correctness_tests": ["python"],
            "iterations": 5,
        })

        assert resp.status_code == 200
        mock_bench.assert_awaited_once()
        assert mock_bench.await_args.kwargs["iterations"] == 5

        body = resp.json()
        small_runs = body["speed_results"][0]["runs"]
        assert len(small_runs) == 5
        # Cold/warm: iteration 1 cold, iterations 2-5 warm
        assert [r["cold_or_warm"] for r in small_runs] == ["cold", "warm", "warm", "warm", "warm"]

        # All 5 raw speed rows persisted (correctness never enters Raw Speed)
        assert store.add_run.call_count == 5
        persisted = [c.args[0] for c in store.add_run.call_args_list]
        assert [r["iteration"] for r in persisted] == [1, 2, 3, 4, 5]
        assert [r["cold_or_warm"] for r in persisted] == ["cold", "warm", "warm", "warm", "warm"]


# ------------------------------------------------------------------
# 4. Three selected speed tests, iterations=5 -> 15 runs total
# ------------------------------------------------------------------

class TestThreeSpeedTestsFifteenRuns:
    def test_small_medium_large_x5_produces_15_runs(self):
        resp, mock_bench, _mock_py, _run, store = _post_evaluation({
            "model": "m",
            "speed_tests": ["small", "medium", "large"],
            "correctness_tests": ["python"],
            "iterations": 5,
        })

        assert resp.status_code == 200

        # run_benchmark executed once per speed test, each with iterations=5
        assert mock_bench.await_count == 3
        for call in mock_bench.await_args_list:
            assert call.kwargs["iterations"] == 5

        body = resp.json()
        names = [s["test_name"] for s in body["speed_results"]]
        assert names == ["small", "medium", "large"]
        for entry in body["speed_results"]:
            assert len(entry["runs"]) == 5

        # 15 raw speed rows persisted (3 tests x 5 iterations)
        assert store.add_run.call_count == 15


# ------------------------------------------------------------------
# 5. Correctness runners are called ONCE regardless of speed iterations
# ------------------------------------------------------------------

class TestCorrectnessSingleRun:
    def test_correctness_runner_called_once(self):
        resp, _mock_bench, mock_py, mock_create_run, _store = _post_evaluation({
            "model": "m",
            "speed_tests": ["small", "medium", "large"],
            "correctness_tests": ["python"],
            "iterations": 5,
        })

        assert resp.status_code == 200
        # Correctness ran exactly once — NOT scaled by speed iteration count
        mock_py.assert_awaited_once()
        mock_create_run.assert_called_once()
        body = resp.json()
        assert len(body["correctness_results"]) == 1
        assert body["correctness_results"][0]["test_type"] == "python"


# ------------------------------------------------------------------
# 6. run_benchmark cold/warm classification + AVG/MIN/MAX from real set
# ------------------------------------------------------------------

class TestBenchmarkColdWarmAndAggregates:
    @pytest.mark.asyncio
    async def test_cold_warm_classification_and_aggregates(self):
        """iteration 1 = cold, iterations 2-5 = warm; aggregates use real values."""
        tps_by_iter = {1: 80.0, 2: 120.0, 3: 140.0, 4: 160.0, 5: 200.0}
        calls = {"n": 0}

        def make_response(i):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = {
                "stats": {
                    "tokens_per_second": tps_by_iter[i],
                    "time_to_first_token_seconds": round(0.05 / i, 4),
                    "input_tokens": 10,
                    "total_output_tokens": 100,
                }
            }
            return r

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def post(url, json=None):
            calls["n"] += 1
            return make_response(calls["n"])

        mock_client.post = post

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await run_benchmark(
                lm_studio_url="http://localhost:1234",
                model="m",
                prompt="p",
                iterations=5,
                max_tokens=100,
                temperature=0.0,
            )

        runs = result["runs"]
        assert [r["cold_or_warm"] for r in runs] == ["cold", "warm", "warm", "warm", "warm"]

        # AVG / MIN / MAX from the real 5-iteration set
        all_tps = list(tps_by_iter.values())
        assert result["aggregate"]["avg_tokens_per_second"] == round(sum(all_tps) / 5, 2)  # 140.0
        assert result["aggregate"]["min_tokens_per_second"] == 80.0
        assert result["aggregate"]["max_tokens_per_second"] == 200.0

        # Warm aggregate excludes iteration 1 (cold)
        warm_tps = all_tps[1:]
        assert result["warm_aggregate"]["available"] is True
        assert result["warm_aggregate"]["avg_tokens_per_second"] == round(sum(warm_tps) / 4, 2)  # 155.0