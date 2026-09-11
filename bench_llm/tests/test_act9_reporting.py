"""Focused regression coverage for Act 9 reporting/comparison integration.

Covers the presentation/reporting layer layered on top of the existing result
path:

- human-readable byte formatting (format_bytes) with missing-data semantics
- context utilisation derivation (context_utilisation_pct) — only when a valid
  denominator exists, never fabricating a percentage
- compact comparison summary (build_comparison_summary) and two-run comparison
- enrichment via /api/results exposing the new fields to the existing view
- historical rows with missing Act 7/8 metadata degrade cleanly (blank/None,
  no crash, no fake zero, no fabricated hardware/context values)
- canonical byte/token fields are preserved through enrichment + persistence

All tests use isolated temp ResultsStore paths so they never touch live data.
"""

import asyncio
import contextlib
import tempfile
from pathlib import Path

import pytest

import src.app_state
from src.results import (
    ResultsStore,
    format_bytes,
    context_utilisation_pct,
    build_comparison_summary,
    enriched_run,
)
from src.routes.results import get_past_results


# ----------------------------------------------------------------------
# Helpers: isolated temp store + row builders (mirrors test_benchmark_metadata.py)
# ----------------------------------------------------------------------

@contextlib.contextmanager
def _temp_paths():
    """Yield (csv_path, db_path) in an isolated temp dir; clean up afterwards."""
    tmp = Path(tempfile.mkdtemp())
    csv_p = tmp / "results.csv"
    db_p = tmp / "results.db"
    try:
        yield csv_p, db_p
    finally:
        for p in (csv_p, db_p):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            tmp.rmdir()
        except OSError:
            pass


def _modern_run(run_id="A1", **overrides):
    row = {
        "timestamp": "2024-01-01T00:00:00Z",
        "run_id": run_id,
        "model_key": "mX",
        "model_display_name": "Model X",
        "model_quantization": "Q4_0",
        "iteration": 1,
        "cold_or_warm": "warm",
        "tokens_per_second": 42.5,
        "ttft_seconds": 0.12,
        "input_tokens": 300,
        "output_tokens": 100,
        "wall_time_seconds": 7.0,
        "prompt_name": "",
        "max_output_tokens": 500,
        "temperature": 0.1,
        "hardware_label": "desk",
        "benchmark_duration_seconds": 7.5,
        # Act 7 context (kept distinct)
        "prompt_tokens": 300,
        "model_max_context": 32000,
        "loaded_context": 32000,
        # Act 7 machine snapshot
        "cpu_model": "Test CPU",
        "cpu_logical_cores": 8,
        "cpu_physical_cores": 4,
        "installed_ram_bytes": 17179869184,   # 16 GiB
        "gpu_model": "Test GPU",
        "total_vram_bytes": 5368709120,       # 5 GiB
        "os_platform": "Linux",
        "python_version": "3.11",
        # Act 8 runtime telemetry
        "system_ram_used_peak_bytes": 9000000000,
        "vram_used_peak_bytes": 3000000000,
        "cpu_util_avg_pct": 33.3,
        "cpu_util_peak_pct": 90.0,
        "gpu_util_avg_pct": 60.0,
        "gpu_util_peak_pct": 95.0,
        "telemetry_sample_count": 20,
    }
    row.update(overrides)
    return row


def _old_run(run_id="OLD"):
    """A pre-Act-7/8 sparse row — no metadata columns at all."""
    return {
        "timestamp": "2023-01-01T00:00:00Z",
        "run_id": run_id,
        "model_key": "mOld",
        "model_display_name": "Old Model",
        "iteration": 1,
        "cold_or_warm": "warm",
        "tokens_per_second": 10.0,
        "ttft_seconds": 0.5,
        "input_tokens": 200,
        "output_tokens": 50,
        "wall_time_seconds": 20.0,
    }


# ----------------------------------------------------------------------
# format_bytes
# ----------------------------------------------------------------------

class TestFormatBytes:
    @pytest.mark.parametrize("value,expected", [
        (None, None),          # hardware/source unavailable -> None (not "0 B")
        ("", ""),              # blank preserved
        (0, "0 B"),            # a genuine measured zero stays 0 B
        (512, "512.0 B"),
        (1024, "1.0 KiB"),
        (1536, "1.5 KiB"),
        (1048576, "1.0 MiB"),
        (5 * 1024 ** 3, "5.0 GiB"),
        (3 * 1024 ** 4, "3.0 TiB"),
        (-1, None),            # negative -> unavailable, not fabricated
        ("abc", None),         # non-numeric -> unavailable
    ])
    def test_contract(self, value, expected):
        assert format_bytes(value) == expected

    def test_parses_numeric_string(self):
        assert format_bytes("2048") == "2.0 KiB"


# ----------------------------------------------------------------------
# context_utilisation_pct
# ----------------------------------------------------------------------

class TestContextUtilisation:
    def test_uses_model_max_context(self):
        assert context_utilisation_pct({"prompt_tokens": 5000, "model_max_context": 32000}) == pytest.approx(15.62)

    def test_falls_back_to_loaded_context(self):
        assert context_utilisation_pct({"prompt_tokens": 5000, "loaded_context": 8000}) == pytest.approx(62.5)

    def test_uses_largest_available_capacity(self):
        # Both present -> denominator is the larger of the two (4000/128000 ~ 3.12%,
        # not 4000/32000 = 12.5%). Rounded to 2dp by the helper.
        row = {"prompt_tokens": 4000, "model_max_context": 32000, "loaded_context": 128000}
        assert context_utilisation_pct(row) == pytest.approx(3.125, abs=0.01)

    def test_over_100_percent_is_valid_when_live_tokens_exceed_capacity(self):
        assert context_utilisation_pct({"prompt_tokens": 40000, "model_max_context": 32000}) == pytest.approx(125.0)

    def test_none_when_no_live_prompt_tokens(self):
        # A zero/absent numerator is treated as unavailable (never a fabricated 0%).
        assert context_utilisation_pct({"prompt_tokens": 0, "model_max_context": 32000}) is None
        assert context_utilisation_pct({"model_max_context": 32000}) is None

    def test_none_when_no_valid_denominator(self):
        # Capacity present but zero -> cannot divide; no fabricated value.
        assert context_utilisation_pct({"prompt_tokens": 500, "loaded_context": 0}) is None
        assert context_utilisation_pct({}) is None


# ----------------------------------------------------------------------
# build_comparison_summary
# ----------------------------------------------------------------------

class TestComparisonSummary:
    def test_old_row_degrades_without_crash_or_fake_values(self):
        summ = build_comparison_summary(_old_run())
        # No fabricated hardware/context — every Act 7/8 field is None/blank.
        assert summ["cpu_model"] == ""
        assert summ["installed_ram_human_readable"] in (None, "")
        assert summ["total_vram_human_readable"] in (None, "")
        assert summ["peak_vram_human_readable"] in (None, "")
        assert summ["context_size"] is None
        assert summ["peak_system_ram_human_readable"] in (None, "")
        assert summ["cpu_util_avg_pct"] is None

    def test_two_runs_with_different_telemetry_are_distinguishable(self):
        run_fast = _modern_run(run_id="A1", installed_ram_bytes=17179869184, gpu_model="GPU A",
                               total_vram_bytes=5368709120, cpu_util_peak_pct=90.0,
                               vram_used_peak_bytes=3000000000, tokens_per_second=80.0)
        run_slow = _modern_run(run_id="A2", installed_ram_bytes=8589934592, gpu_model="GPU B",
                               total_vram_bytes=2684354560, cpu_util_peak_pct=45.0,
                               system_ram_used_peak_bytes=4500000000,
                               vram_used_peak_bytes=1500000000, tokens_per_second=20.0)
        s_fast = build_comparison_summary(run_fast)
        s_slow = build_comparison_summary(run_slow)

        # Machine identity differs.
        assert s_fast["gpu_model"] != s_slow["gpu_model"]
        assert s_fast["installed_ram_human_readable"] != s_slow["installed_ram_human_readable"]
        # Resource peaks differ (canonical bytes preserved).
        assert s_fast["peak_vram_bytes"] > s_slow["peak_vram_bytes"]
        assert s_fast["peak_system_ram_human_readable"] != s_slow["peak_system_ram_human_readable"]
        # Utilisation / decode speed differ.
        assert s_fast["cpu_util_peak_pct"] != s_slow["cpu_util_peak_pct"]
        assert s_fast["tokens_per_second"] == 80.0
        assert s_slow["tokens_per_second"] == 20.0

    def test_canonical_byte_values_preserved_in_summary(self):
        summ = build_comparison_summary(_modern_run())
        assert summ["installed_ram_bytes"] == 17179869184
        assert summ["total_vram_bytes"] == 5368709120
        assert summ["peak_vram_bytes"] == 3000000000


# ----------------------------------------------------------------------
# enriched_run
# ----------------------------------------------------------------------

class TestEnrichedRun:
    def test_does_not_mutate_input_and_preserves_canonical_bytes(self):
        row = _modern_run()
        snapshot = dict(row)
        enriched = enriched_run(row)
        assert row == snapshot                      # input untouched
        assert enriched["installed_ram_bytes"] == 17179869184   # canonical preserved
        assert enriched["total_vram_bytes"] == 5368709120

    def test_adds_presentation_keys(self):
        enriched = enriched_run(_modern_run())
        assert enriched["installed_ram"] == "16.0 GiB"
        assert enriched["total_vram"] == "5.0 GiB"
        assert isinstance(enriched["peak_vram"], str) and "GiB" in enriched["peak_vram"]
        assert enriched["context_utilisation_pct"] == pytest.approx(context_utilisation_pct(_modern_run()))
        assert "comparison_summary" in enriched


# ----------------------------------------------------------------------
# /api/results integration through the existing reporting path
# ----------------------------------------------------------------------

def _run_api(store):
    original = src.app_state.results_store
    src.app_state.results_store = store
    try:
        return asyncio.run(get_past_results())["results"]
    finally:
        src.app_state.results_store = original


class TestApiResultsEnrichment:
    def test_modern_run_exposes_new_fields(self):
        with _temp_paths() as (csv_p, db_p):
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            store.add_run(_modern_run())
            rows = _run_api(store)

        a1 = next(r for r in rows if r["run_id"] == "A1")
        assert a1["context_utilisation_pct"] is not None
        assert isinstance(a1["installed_ram"], str) and a1["installed_ram"]
        assert a1["peak_vram"]  # human-readable VRAM string
        cs = a1["comparison_summary"]
        for key in ("model", "model_quantization", "context_size", "prompt_tokens",
                    "ttft_seconds", "tokens_per_second", "benchmark_duration_seconds",
                    "peak_system_ram_human_readable", "peak_vram_human_readable",
                    "cpu_util_avg_pct", "cpu_util_peak_pct", "gpu_util_avg_pct",
                    "gpu_util_peak_pct"):
            assert key in cs

    def test_old_row_degrades_without_crash_or_fake_zero(self):
        with _temp_paths() as (csv_p, db_p):
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            store.add_run(_old_run())
            rows = _run_api(store)

        old = next(r for r in rows if r["run_id"] == "OLD")
        assert old.get("context_utilisation_pct") is None      # missing != 0
        assert old.get("installed_ram") in (None, "")          # no fabricated hardware
        assert old.get("peak_vram") in (None, "")
        # No exception, summary present but all-missing.
        assert isinstance(old.get("comparison_summary"), dict)

    def test_canonical_bytes_survive_through_api(self):
        with _temp_paths() as (csv_p, db_p):
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            store.add_run(_modern_run())
            rows = _run_api(store)
        a1 = next(r for r in rows if r["run_id"] == "A1")
        assert a1["installed_ram_bytes"] == 17179869184
        assert a1["total_vram_bytes"] == 5368709120


# ----------------------------------------------------------------------
# Persistence round-trip (canonical fields only; derived values recompute)
# ----------------------------------------------------------------------

class TestPersistenceRoundTrip:
    def test_act7_8_canonical_fields_survive_reload(self):
        with _temp_paths() as (csv_p, db_p):
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            store.add_run(_modern_run())

            # A fresh instance reads from the same DB — source of truth.
            reloaded = ResultsStore(csv_path=csv_p, db_path=db_p)
            rows = {r["run_id"]: r for r in reloaded.get_all()}

        a1 = rows["A1"]
        assert a1["installed_ram_bytes"] == 17179869184
        assert a1["total_vram_bytes"] == 5368709120
        assert a1["system_ram_used_peak_bytes"] == 9000000000
        assert a1["cpu_util_avg_pct"] == 33.3
        # Derived value recomputed identically after reload.
        assert context_utilisation_pct(a1) == pytest.approx(context_utilisation_pct(_modern_run()))
