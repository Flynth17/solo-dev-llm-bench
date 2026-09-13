"""Act 20 -- corrected Standard Speed metric semantics (unit-level).

Full-prefill sample is separated from warm/cache-reused generation samples; a cached TTFT
must never become full-prefill throughput; a deterministic cache-buster prefix defeats LCP/KV
reuse for the authoritative request; and runs carry a backward-safe ``speed_metric_version`` so
legacy cached-TTFT prefill is flagged on the result page without hiding historical telemetry.

These exercise :func:`src.v2_speed_suite_runner._aggregate_point` / ``_speed_cache_buster`` and
the read-model projection directly -- no network, no real benchmark.
"""

from __future__ import annotations

import pytest

import src.v2_speed_suite_runner as vs
from src.v2_speed_read_model import load_speed_run_by_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bench(*runs):
    """Build a run_benchmark-shaped dict from explicit per-iteration run dicts.

    warm_aggregate / aggregate mirror what :func:`run_benchmark` computes so
    ``_aggregate_point`` consumes them exactly as it would in production.
    """
    runs = list(runs)
    warm_tps = [r["tokens_per_second"] for r in runs
                if r.get("cold_or_warm") == "warm" and r.get("tokens_per_second", 0) > 0]
    warm_ttft = [r["ttft_seconds"] for r in runs if r.get("cold_or_warm") == "warm"]
    overall_tps = [r["tokens_per_second"] for r in runs if r.get("tokens_per_second", 0) > 0]

    warm_aggregate = {
        "avg_tokens_per_second": round(sum(warm_tps) / len(warm_tps), 2) if warm_tps else None,
        "avg_ttft": round(sum(warm_ttft) / len(warm_ttft), 2) if warm_ttft else None,
        "available": bool(warm_tps),
    }
    overall = {
        "avg_tokens_per_second": round(sum(overall_tps) / len(overall_tps), 2) if overall_tps else 0,
        "min_tokens_per_second": round(min(overall_tps), 2) if overall_tps else 0,
        "max_tokens_per_second": round(max(overall_tps), 2) if overall_tps else 0,
    }
    wall = sum(r.get("wall_time_seconds", 0.0) for r in runs) + 0.1
    return {
        "runs": runs,
        "warm_aggregate": warm_aggregate,
        "aggregate": overall,
        "benchmark_duration_seconds": round(wall, 4),
    }


def _row(target, input_tokens, output_tokens, ttft, tps=185.0, version=None):
    base = dict(
        run_id="speed-xyz", model_key="m", model_display_name="m",
        target_context_tokens=target, input_tokens=input_tokens, output_tokens=output_tokens,
        ttft_seconds=ttft, prefill_tokens_per_second=round(input_tokens / ttft, 1),
        tokens_per_second=tps, wall_time_seconds=8.0, cold_or_warm="warm",
        speed_point_status="completed",
    )
    if version is not None:
        base["speed_metric_version"] = version
    return base


def _run_rows(version):
    return [
        _row(8192, 5700, 55, 0.692220, tps=40.0, version=version),
        _row(16384, 11300, 53, 0.69, tps=185.0, version=version),
        _row(32768, 22600, 63, 0.71, tps=184.0, version=version),
    ]


# ---------------------------------------------------------------------------
# Full-prefill sample separation (tests 1-4)
# ---------------------------------------------------------------------------

class TestFullPrefillSeparation:
    def test_regression_full_prefill_number(self):
        # Live evidence: 5700 tokens evaluated in 0.692220 s -> ~8234.4 tok/s (real full-prefill).
        bench = _bench(
            {"cold_or_warm": "cold", "iteration": 1, "input_tokens": 5700,
             "ttft_seconds": 0.692220, "tokens_per_second": 40.0},
            {"cold_or_warm": "warm", "iteration": 2, "input_tokens": 5700,
             "ttft_seconds": 0.036, "tokens_per_second": 185.0},
        )
        agg = vs._aggregate_point(bench)
        assert abs(agg["prefill_tokens_per_second"] - round(5700 / 0.692220, 1)) < 0.6

    def test_cached_ttft_is_excluded_from_prefill(self):
        # A warm (cache-reused) TTFT of 0.035952 s would imply a ~158k tok/s fake prefill; it
        # must NOT become full-prefill throughput -- the cold sample's rate is authoritative.
        bench = _bench(
            {"cold_or_warm": "cold", "iteration": 1, "input_tokens": 5700,
             "ttft_seconds": 0.692220, "tokens_per_second": 40.0},
            {"cold_or_warm": "warm", "iteration": 2, "input_tokens": 5700,
             "ttft_seconds": 0.035952, "tokens_per_second": 185.0},
        )
        agg = vs._aggregate_point(bench)
        assert agg["prefill_tokens_per_second"] < 20000
        assert abs(agg["prefill_tokens_per_second"] - round(5700 / 0.692220, 1)) < 0.6

    def test_primary_ttft_comes_only_from_full_sample(self):
        # Warm TTFTs of 0.036/0.028 must never become the reported primary TTFT; the cold
        # (full-prefill) sample's 0.692220 is authoritative.
        bench = _bench(
            {"cold_or_warm": "cold", "iteration": 1, "input_tokens": 5700,
             "ttft_seconds": 0.692220, "tokens_per_second": 40.0},
            {"cold_or_warm": "warm", "iteration": 2, "input_tokens": 5700,
             "ttft_seconds": 0.036, "tokens_per_second": 185.0},
            {"cold_or_warm": "warm", "iteration": 3, "input_tokens": 5700,
             "ttft_seconds": 0.028, "tokens_per_second": 186.0},
        )
        agg = vs._aggregate_point(bench)
        assert abs(agg["ttft_seconds"] - round(0.692220, 4)) < 1e-6

    def test_warm_ttft_cannot_change_prefill(self):
        # Varying warm TTFTs must not move the prefill number at all -- it only reads cold.
        bench_a = _bench(
            {"cold_or_warm": "cold", "input_tokens": 5700, "ttft_seconds": 0.692220,
             "tokens_per_second": 40.0},
            {"cold_or_warm": "warm", "input_tokens": 5700, "ttft_seconds": 0.01,
             "tokens_per_second": 185.0},
        )
        bench_b = _bench(
            {"cold_or_warm": "cold", "input_tokens": 5700, "ttft_seconds": 0.692220,
             "tokens_per_second": 40.0},
            {"cold_or_warm": "warm", "input_tokens": 5700, "ttft_seconds": 0.5,
             "tokens_per_second": 185.0},
        )
        pa = vs._aggregate_point(bench_a)
        pb = vs._aggregate_point(bench_b)
        assert pa["prefill_tokens_per_second"] == pb["prefill_tokens_per_second"]


# ---------------------------------------------------------------------------
# Generation throughput (tests 5) -- warm mean, never derived from TTFT.
# ---------------------------------------------------------------------------

class TestGenerationAggregation:
    def test_generation_uses_warm_mean_deterministic(self):
        # Generation throughput is the deterministic mean of warm decode samples; the cold
        # full-prefill request's (999 tok/s) decode is excluded from that aggregate.
        bench = _bench(
            {"cold_or_warm": "cold", "iteration": 1, "input_tokens": 5700,
             "ttft_seconds": 0.692220, "tokens_per_second": 999.0},
            {"cold_or_warm": "warm", "iteration": 2, "input_tokens": 5700,
             "ttft_seconds": 0.036, "tokens_per_second": 180.0},
            {"cold_or_warm": "warm", "iteration": 3, "input_tokens": 5700,
             "ttft_seconds": 0.034, "tokens_per_second": 190.0},
        )
        agg = vs._aggregate_point(bench)
        assert abs(agg["generation_tokens_per_second"] - 185.0) < 1e-6

    def test_generation_not_derived_from_ttft(self):
        bench = _bench(
            {"cold_or_warm": "cold", "input_tokens": 5700, "ttft_seconds": 0.692220,
             "tokens_per_second": 40.0},
            {"cold_or_warm": "warm", "input_tokens": 5700, "ttft_seconds": 0.036,
             "tokens_per_second": 185.0},
        )
        agg = vs._aggregate_point(bench)
        assert agg["generation_tokens_per_second"] == pytest.approx(185.0, rel=1e-6)


# ---------------------------------------------------------------------------
# Cache-buster design (tests 6-9)
# ---------------------------------------------------------------------------

class TestCacheBuster:
    def test_deterministic_same_run_and_point(self):
        assert vs._speed_cache_buster("speed-abc", 8192) == vs._speed_cache_buster("speed-abc", 8192)

    def test_differs_across_run_ids(self):
        assert vs._speed_cache_buster("speed-aaa", 8192) != vs._speed_cache_buster("speed-bbb", 8192)

    def test_differs_across_points(self):
        assert vs._speed_cache_buster("speed-abc", 8192) != vs._speed_cache_buster("speed-abc", 32768)

    def test_is_a_prefix_before_shared_filler(self):
        plain = vs.build_context_pressure_prompt(8192)
        buster = vs._speed_cache_buster("speed-abc", 8192)
        combined = buster + plain
        assert combined.startswith(buster)      # cache-buster sits at the START of the prompt
        assert combined != plain                # it actually changes the prompt
        assert len(buster) < len(plain)         # negligible relative to the payload


# ---------------------------------------------------------------------------
# Metric versioning + legacy warning at read-model level (tests 10-13, 11)
# ---------------------------------------------------------------------------

class TestMetricVersioning:
    def test_corrected_run_has_no_legacy_warning(self):
        result = load_speed_run_by_id("speed-xyz", _run_rows(2))
        assert result["speed_metric_version"] == 2
        assert result["legacy_prefill_warning"] is False
        assert all(not p["legacy_prefill_warning"] for p in result["points"])

    def test_legacy_run_signals_warning_and_preserves_stored_values(self):
        # Historical rows carry no metric version (None/blank); they stay readable and their
        # stored prefill values are preserved verbatim while flagging legacy semantics.
        rows = _run_rows(None)
        result = load_speed_run_by_id("speed-xyz", rows)
        assert result["speed_metric_version"] is None
        assert result["legacy_prefill_warning"] is True
        assert all(p["legacy_prefill_warning"] for p in result["points"])
        # Stored prefill throughput is preserved unchanged (not recomputed, not hidden).
        assert [p["prefill_tokens_per_second"] for p in result["points"]] == \
            [round(5700 / 0.692220, 1), round(11300 / 0.69, 1), round(22600 / 0.71, 1)]

    def test_legacy_version_one_also_flags_warning(self):
        # Explicit version 1 is legacy semantics too (only >= 2 is corrected).
        rows = _run_rows(1)
        result = load_speed_run_by_id("speed-xyz", rows)
        assert result["legacy_prefill_warning"] is True
