"""Regression tests for Standard Speed ``run_benchmark`` iteration semantics.

These exercise the active benchmark engine directly (no legacy evaluation route):

- run_benchmark classifies iteration 1 = cold and iterations 2+ = warm, and computes
  AVG / MIN / MAX from the real iteration set (warm aggregate excludes iter 1)

The portions of this file that previously drove the retired unified Evaluation Suite
``/api/evaluation/run`` were removed in Act 24.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from src.benchmark import run_benchmark


# ------------------------------------------------------------------
# run_benchmark cold/warm classification + AVG/MIN/MAX from real set
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
