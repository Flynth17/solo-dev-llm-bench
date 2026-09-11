"""Focused regression coverage for Act 8 runtime utilisation telemetry.

Covers, using fully mocked hardware/resource sources (no real machine load, no real
timing):

- sampler start/stop lifecycle and daemon-thread cleanup
- system RAM start / peak / end
- current-process RSS start / peak / end
- GPU VRAM start / peak / end
- CPU utilisation avg / peak
- GPU utilisation avg / peak
- sample count
- unavailable metrics degrade to None (never fabricated)
- stop() stays safe and cleans up even when a tick raises
- the pure cpu_util_pct_from_delta formula + guards
- invocation-level telemetry attached identically to every result row via the route
- additive-schema compatibility (old rows load; new fields persist)

All tests use isolated temp ResultsStore paths so they never touch live benchmark data.
"""

import asyncio
import contextlib
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

import src.app_state
from src.results import ResultsStore, CSV_HEADERS, OPTIONAL_METADATA_COLUMNS
from src.telemetry import (
    TelemetrySampler,
    TELEMETRY_SAMPLE_INTERVAL,
    cpu_util_pct_from_delta,
)


# The 14 Act 8 runtime utilisation columns attached to every invocation row.
ACT8_TELEMETRY_COLS = [
    "system_ram_used_start_bytes", "system_ram_used_peak_bytes", "system_ram_used_end_bytes",
    "process_rss_start_bytes", "process_rss_peak_bytes", "process_rss_end_bytes",
    "vram_used_start_bytes", "vram_used_peak_bytes", "vram_used_end_bytes",
    "cpu_util_avg_pct", "cpu_util_peak_pct",
    "gpu_util_avg_pct", "gpu_util_peak_pct",
    "telemetry_sample_count",
]


# ----------------------------------------------------------------------
# Helpers: isolated temp store + deterministic sampler driver
# ----------------------------------------------------------------------

def _temp_paths():
    """Generator yielding a single (csv_path, db_path) pair per iteration."""
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


# Short aliases accepted by _tick() mapped onto the real sampler-tick key names.
_TICK_ALIASES = {
    "ram": "system_ram_used_bytes",
    "rss": "process_rss_bytes",
    "vram": "vram_used_bytes",
    "cpu": "cpu_util_pct",
    "gpu": "gpu_util_pct",
}


def _tick(**kw) -> dict:
    """Build one canned sampler tick from short keys (ram/rss/vram/cpu/gpu); only the
    provided keys are set, every other key stays None."""
    d = {
        "system_ram_used_bytes": None,
        "process_rss_bytes": None,
        "vram_used_bytes": None,
        "gpu_util_pct": None,
        "cpu_util_pct": None,
    }
    for short, val in kw.items():
        d[_tick_alias(short)] = val
    return d


def _tick_alias(short):
    try:
        return _TICK_ALIASES[short]
    except KeyError as exc:  # pragma: no cover - defensive: fail loudly on typos
        raise KeyError(f"unknown sampler-tick key {short!r}") from exc


def _canned_faker(samples):
    """Return a ``_sample_tick`` replacement replaying *samples* in order.

    On the final canned value it sets the stop event so the sampler loop drains all
    samples then exits naturally — making the sample count deterministic regardless of
    host scheduling or real timing."""
    state = {"i": 0}

    def fake_tick(self, *args, **kwargs):
        value = samples[state["i"]]
        state["i"] += 1
        if state["i"] >= len(samples):
            self._stop_event.set()
        return dict(value)

    return fake_tick


@contextlib.contextmanager
def run_sampler(samples, *, ram=None, rss=None, gpu=None, cpu=None):
    """Yield a zero-interval TelemetrySampler with all resource sources patched to
    canned values and ``_sample_tick`` replaying *samples*."""
    if gpu is None:
        gpu = {"vram_used_bytes": None, "gpu_util_pct": None}
    sampler = TelemetrySampler(sample_interval=0)
    with patch("src.telemetry.read_system_ram_used_bytes", return_value=ram), \
         patch("src.telemetry.read_process_rss_bytes", return_value=rss), \
         patch("src.telemetry._nvidia_gpu_metrics", return_value=gpu), \
         patch("src.telemetry.read_system_cpu_cumulative", return_value=cpu), \
         patch.object(TelemetrySampler, "_sample_tick", new=_canned_faker(samples)):
        yield sampler


def _drain_and_stop(sampler):
    """Start the sampler, wait for it to drain its canned samples, then stop it.

    Call this while patches are still active (inside a ``run_sampler`` block)."""
    sampler.start()
    if sampler._thread is not None:
        sampler._thread.join(timeout=5)
    return sampler.stop()


def _assert_complete_dict(tele):
    """Every Act 8 telemetry key must be present in the aggregated dict."""
    for col in ACT8_TELEMETRY_COLS:
        assert col in tele, f"missing key {col}"


# ----------------------------------------------------------------------
# Sampler lifecycle + sample count + thread cleanup
# ----------------------------------------------------------------------

class TestSamplerLifecycle:
    def test_sample_interval_constant_is_sensible(self):
        # Default cadence sits inside the intended 0.5-1.0s window (not blocking).
        assert TELEMETRY_SAMPLE_INTERVAL == 1.0

    def test_start_spawns_daemon_thread_and_stop_cleans_up(self):
        with run_sampler([_tick(ram=10)], ram=123) as sampler:
            sampler.start()
            assert sampler._thread is not None and sampler._thread.daemon is True
            tele = _drain_and_stop(sampler)
        # stop() joins and clears the background thread; nothing leaks.
        assert sampler._thread is None
        _assert_complete_dict(tele)

    def test_sample_count_matches_canned_ticks(self):
        samples = [_tick(ram=10), _tick(ram=20), _tick(ram=30)]
        with run_sampler(samples, ram=5) as sampler:
            tele = _drain_and_stop(sampler)
        assert tele["telemetry_sample_count"] == len(samples)


# ----------------------------------------------------------------------
# System RAM: start / peak / end (boundaries come from collectors;
# peak is computed only over sampled ticks — boundaries are excluded).
# ----------------------------------------------------------------------

class TestRamTelemetry:
    def test_ram_start_end_and_peak(self):
        # Boundaries read 999 (higher than any sample) to prove they do NOT feed the peak.
        with run_sampler([_tick(ram=90), _tick(ram=150)], ram=999) as sampler:
            tele = _drain_and_stop(sampler)
        assert tele["system_ram_used_start_bytes"] == 999
        assert tele["system_ram_used_end_bytes"] == 999
        assert tele["system_ram_used_peak_bytes"] == 150   # max of sampled ticks only


# ----------------------------------------------------------------------
# Current-process RSS: start / peak / end
# ----------------------------------------------------------------------

class TestRssTelemetry:
    def test_rss_start_end_and_peak(self):
        with run_sampler([_tick(rss=400), _tick(rss=500)], rss=999) as sampler:
            tele = _drain_and_stop(sampler)
        assert tele["process_rss_start_bytes"] == 999
        assert tele["process_rss_end_bytes"] == 999
        assert tele["process_rss_peak_bytes"] == 500


# ----------------------------------------------------------------------
# GPU VRAM: start / peak / end
# ----------------------------------------------------------------------

class TestVramTelemetry:
    def test_vram_start_end_and_peak(self):
        gpu = {"vram_used_bytes": 999, "gpu_util_pct": None}
        with run_sampler([_tick(vram=700), _tick(vram=850)], gpu=gpu) as sampler:
            tele = _drain_and_stop(sampler)
        assert tele["vram_used_start_bytes"] == 999
        assert tele["vram_used_end_bytes"] == 999
        assert tele["vram_used_peak_bytes"] == 850


# ----------------------------------------------------------------------
# CPU utilisation avg / peak (computed over per-tick samples on stop())
# ----------------------------------------------------------------------

class TestCpuUtilisation:
    def test_cpu_avg_and_peak(self):
        with run_sampler([_tick(cpu=10), _tick(cpu=30), _tick(cpu=50)]) as sampler:
            tele = _drain_and_stop(sampler)
        assert tele["cpu_util_avg_pct"] == 30.0       # (10+30+50)/3, rounded to 2dp
        assert tele["cpu_util_peak_pct"] == 50


# ----------------------------------------------------------------------
# GPU utilisation avg / peak
# ----------------------------------------------------------------------

class TestGpuUtilisation:
    def test_gpu_avg_and_peak(self):
        with run_sampler([_tick(gpu=10.0), _tick(gpu=20.0), _tick(gpu=30.0)]) as sampler:
            tele = _drain_and_stop(sampler)
        assert tele["gpu_util_avg_pct"] == 20.0
        assert tele["gpu_util_peak_pct"] == 30.0


# ----------------------------------------------------------------------
# Unavailable metrics degrade to None (never fabricated), count still recorded
# ----------------------------------------------------------------------

class TestUnavailableTelemetry:
    def test_all_unavailable_returns_none_metrics_with_count(self):
        none_gpu = {"vram_used_bytes": None, "gpu_util_pct": None}
        samples = [_tick(), _tick(), _tick()]
        with run_sampler(samples, ram=None, rss=None, gpu=none_gpu, cpu=None) as sampler:
            tele = _drain_and_stop(sampler)
        for col in ACT8_TELEMETRY_COLS:
            if col == "telemetry_sample_count":
                assert tele[col] == len(samples)      # count is still meaningful
            else:
                assert tele[col] is None, f"{col} should be None when unavailable"


# ----------------------------------------------------------------------
# stop() stays safe and cleans up even when a tick raises (route's finally contract)
# ----------------------------------------------------------------------

class TestStopSafeOnException:
    def test_stop_is_safe_and_cleans_up_even_when_ticks_raise(self):
        def bad_tick(self, *args, **kwargs):
            raise RuntimeError("boom")

        sampler = TelemetrySampler(sample_interval=0)
        old_hook = threading.excepthook
        threading.excepthook = lambda arg: None   # suppress daemon-thread stderr noise
        try:
            with patch("src.telemetry.read_system_ram_used_bytes", return_value=None), \
                 patch("src.telemetry.read_process_rss_bytes", return_value=None), \
                 patch("src.telemetry._nvidia_gpu_metrics",
                       return_value={"vram_used_bytes": None, "gpu_util_pct": None}), \
                 patch("src.telemetry.read_system_cpu_cumulative", return_value=None), \
                 patch.object(TelemetrySampler, "_sample_tick", new=bad_tick):
                sampler.start()
                tele = sampler.stop()          # must NOT raise despite the raising tick
        finally:
            threading.excepthook = old_hook
        assert sampler._thread is None               # thread cleaned up (no leak)
        _assert_complete_dict(tele)                  # complete dict still returned
        assert tele["telemetry_sample_count"] == 0   # no partial sample recorded after raise
        assert tele["system_ram_used_peak_bytes"] is None


# ----------------------------------------------------------------------
# Pure CPU utilisation formula + guards (no threading, no hardware)
# ----------------------------------------------------------------------

class TestCpuUtilFormula:
    def test_utilisation_over_interval(self):
        prev = {"total": 100, "idle": 80}     # delta_total=100, delta_idle=60 -> 40%
        cur = {"total": 200, "idle": 140}
        assert cpu_util_pct_from_delta(prev, cur) == 40.0

    def test_none_baseline_or_cur_returns_none(self):
        assert cpu_util_pct_from_delta(None, {"total": 1, "idle": 0}) is None
        assert cpu_util_pct_from_delta({"total": 1, "idle": 0}, None) is None

    def test_non_positive_total_delta_returns_none(self):
        base = {"total": 10, "idle": 5}
        assert cpu_util_pct_from_delta(base, dict(base)) is None


# ----------------------------------------------------------------------
# Invocation-level telemetry attached consistently to every row (via the route)
# ----------------------------------------------------------------------

class TestInvocationTelemetryAttachedToRows:
    def test_identical_invocation_telemetry_on_every_row(self):
        import src.routes.benchmark as rb

        fake_tele = {col: None for col in ACT8_TELEMETRY_COLS}
        # Concrete values to detect accidental per-row overwrite / clobbering.
        fake_tele["system_ram_used_peak_bytes"] = 2468013579
        fake_tele["cpu_util_avg_pct"] = 33.33
        fake_tele["telemetry_sample_count"] = 4

        fake_result = {
            "run_id": "inv-1", "timestamp": "2026-01-01T00:00:00+00:00", "model": "m",
            "benchmark_duration_seconds": 9.5,
            "runs": [
                {"iteration": 1, "cold_or_warm": "cold", "tokens_per_second": 50.0,
                 "ttft_seconds": 0.2, "input_tokens": 100, "output_tokens": 40,
                 "model_load_time_seconds": 1.0, "wall_time_seconds": 2.0},
                {"iteration": 2, "cold_or_warm": "warm", "tokens_per_second": 60.0,
                 "ttft_seconds": 0.15, "input_tokens": 120, "output_tokens": 48,
                 "model_load_time_seconds": 1.2, "wall_time_seconds": 2.2},
            ],
        }

        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            sampler_mock = MagicMock()
            sampler_mock.stop.return_value = fake_tele
            with patch.object(src.app_state, "results_store", store), \
                 patch("src.routes.benchmark.run_benchmark", new=AsyncMock(return_value=fake_result)), \
                 patch("src.routes.benchmark.resolve_persisted_quantization",
                       new=AsyncMock(return_value="Q4_K_M")), \
                 patch("src.routes.benchmark.resolve_context_capacity",
                       new=AsyncMock(return_value={"model_max_context": None, "loaded_context": None})), \
                 patch("src.routes.benchmark.TelemetrySampler", return_value=sampler_mock):
                asyncio.run(rb.run_benchmark_endpoint({
                    "model": "m", "prompt": "p", "iterations": 2, "max_tokens": 500,
                    "temperature": 0.0, "lm_studio_url": "http://localhost:1234",
                    "hardware_label": "HW", "execution_environment": "Local",
                    "connection_type": "Local", "prompt_name": "p",
                }))

            rows = store.get_all()
            assert len(rows) == 2
            # Every row carries the invocation-level telemetry, identical to the sampler output.
            for r in rows:
                for col in ACT8_TELEMETRY_COLS:
                    assert r[col] == fake_tele[col], f"{col}: {r.get(col)} != {fake_tele[col]}"
            # Invocation-level value is shared across rows (not recomputed per row)...
            assert rows[0]["system_ram_used_peak_bytes"] == rows[1]["system_ram_used_peak_bytes"]
            assert rows[0]["benchmark_duration_seconds"] == 9.5 == rows[1]["benchmark_duration_seconds"]
            # ...while per-iteration fields are preserved (not overwritten by invocation telemetry).
            assert rows[0]["iteration"] == 1 and rows[1]["iteration"] == 2
            assert rows[0]["input_tokens"] == 100 and rows[1]["input_tokens"] == 120


# ----------------------------------------------------------------------
# Additive schema compatibility (old rows load; new fields persist)
# ----------------------------------------------------------------------

class TestAdditiveSchemaCompatibility:
    def test_act8_columns_registered_as_additive_metadata(self):
        optional_names = [c for c, _ in OPTIONAL_METADATA_COLUMNS]
        for col in ACT8_TELEMETRY_COLS:
            assert col in CSV_HEADERS          # serialised to the CSV mirror
            assert col in optional_names       # registered as an additive metadata column

    def test_old_schema_db_migrates_and_historical_rows_load_none(self):
        tmp = Path(tempfile.mkdtemp())
        csv_p = tmp / "results.csv"
        db_p = tmp / "old.db"

        base_cols = [c for c in CSV_HEADERS if c not in ACT8_TELEMETRY_COLS]
        conn = sqlite3.connect(str(db_p))
        try:
            # A database created BEFORE Act 8: runs table with only the older columns.
            # A database created BEFORE Act 8: runs table with only the older columns
            # (plus the auto-increment id that _load_from_db orders by).
            conn.execute(
                f"CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                f"{', '.join(f'{c} TEXT' for c in base_cols)})"
            )
            conn.execute(
                "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.execute(
                "INSERT INTO runs (id, timestamp, run_id, model_key) VALUES (?, ?, ?, ?)",
                (1, "2019-01-01T00:00:00+00:00", "legacy-row", "legacy-model"),
            )
            conn.execute(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                ("csv_migrated", "1"),   # mark migrated so CSV is never re-read
            )
            conn.commit()
        finally:
            conn.close()

        store = ResultsStore(csv_path=csv_p, db_path=db_p)  # ALTERs in the Act 8 columns
        rows = store.get_all()
        assert len(rows) == 1                              # historical row preserved (no crash)
        legacy = rows[0]
        for col in ACT8_TELEMETRY_COLS:
            assert legacy.get(col) in (None, ""), f"legacy {col} should be blank/None, got {legacy.get(col)!r}"

    def test_act8_columns_persist_and_round_trip(self):
        values = {
            "system_ram_used_start_bytes": 1000, "system_ram_used_peak_bytes": 2000,
            "system_ram_used_end_bytes": 1500,
            "process_rss_start_bytes": 3000, "process_rss_peak_bytes": 4000,
            "process_rss_end_bytes": 3500,
            "vram_used_start_bytes": 5000, "vram_used_peak_bytes": 6000,
            "vram_used_end_bytes": 5500,
            "cpu_util_avg_pct": 12.5, "cpu_util_peak_pct": 90.0,
            "gpu_util_avg_pct": 7.5, "gpu_util_peak_pct": 88.0,
            "telemetry_sample_count": 5,
        }
        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            run = {"timestamp": "2026-01-01T00:00:00+00:00", "run_id": "act8-r1",
                    "model_key": "m", **values}
            store.add_run(run)
            row = [r for r in store.get_all() if r["run_id"] == "act8-r1"][0]
            for col, expected in values.items():
                assert row[col] == expected, f"{col}: {row.get(col)} != {expected}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
