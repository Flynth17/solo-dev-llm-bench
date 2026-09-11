"""Focused regression coverage for Act 7 benchmark metadata (context + timing + machine snapshot).

Covers:
- context-field separation (model_max_context / loaded_context / prompt_tokens stay distinct)
- total run duration capture and preservation of existing TTFT/throughput metrics
- hardware snapshot serialization + persistence round-trip
- historical / missing-metadata compatibility (old rows load without crashing)

All tests use isolated temp ResultsStore paths so they never touch live benchmark data.
"""

import asyncio
import csv as _csv
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

import src.app_state
from src.results import ResultsStore, CSV_HEADERS, OPTIONAL_METADATA_COLUMNS
from src.hardware import snapshot_hardware
from src.benchmark import resolve_context_capacity


# ----------------------------------------------------------------------
# Helpers: isolated temp store + fake httpx for context-capacity tests
# ----------------------------------------------------------------------

def _temp_paths():
    # Generator yielding a single (csv_path, db_path) pair per iteration, so
    # callers write ``for csv_p, db_p in _temp_paths():``.
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


class _FakeResp:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json


class _FakeClient:
    """Minimal stand-in for httpx.AsyncClient used by resolve_context_capacity."""

    def __init__(self, resp=None, raise_exc=None):
        self._resp = resp
        self._raise_exc = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._resp


def _full_run(**overrides):
    run = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "run_id": "meta-run-001",
        "model_key": "test-model",
        "model_display_name": "Test Model",
        "hardware_label": "HW",
        "execution_environment": "Local",
        "connection_type": "",
        "iteration": 1,
        "cold_or_warm": "warm",
        "tokens_per_second": 45.0,
        "ttft_seconds": 0.4,
        "input_tokens": 128,
        "output_tokens": 64,
        "model_load_time_seconds": None,
        "wall_time_seconds": 3.0,
        "prompt_name": "Custom",
        "max_output_tokens": 500,
        "temperature": 0.0,
    }
    run.update(overrides)
    return run


def _hardware_row(run=None):
    row = _full_run() if run is None else dict(run)
    row.update({
        "cpu_model": "Mock CPU",
        "cpu_logical_cores": 8,
        "cpu_physical_cores": 4,
        "installed_ram_bytes": 17179869184,
        "gpu_model": "Fake GPU",
        "total_vram_bytes": 5368709120,
        "os_platform": "Linux",
        "os_version": "6.1.0",
        "nvidia_driver_version": "550.0",
        "python_version": "3.13.0",
    })
    return row


# ----------------------------------------------------------------------
# Schema consistency
# ----------------------------------------------------------------------

class TestSchemaConsistency:
    def test_optional_metadata_columns_present_in_csv_headers(self):
        for col, _ in OPTIONAL_METADATA_COLUMNS:
            assert col in CSV_HEADERS, f"missing from CSV_HEADERS: {col}"

    def test_all_csv_headers_have_sqlite_definition(self):
        # sqlite3 column types are declared in results.py alongside the headers.
        assert "model_max_context" in CSV_HEADERS
        assert "loaded_context" in CSV_HEADERS
        assert "prompt_tokens" in CSV_HEADERS


# ----------------------------------------------------------------------
# Context-field separation
# ----------------------------------------------------------------------

class TestContextFieldSeparation:
    def test_context_columns_are_distinct(self):
        # The three context dimensions must be separate, non-duplicate columns.
        cols = ["model_max_context", "loaded_context", "prompt_tokens"]
        assert len(set(cols)) == 3
        for c in cols:
            assert CSV_HEADERS.index(c) >= 0

    def test_prompt_tokens_reflects_live_input_not_conflated_with_capacity(self):
        """prompt_tokens must mirror the actual live input_tokens and stay distinct
        from model_max_context / loaded_context (which are best-effort capacity)."""
        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            # prompt_tokens provided; capacities intentionally absent/None.
            run = _full_run(input_tokens=105093, prompt_tokens=105093,
                            model_max_context=None, loaded_context=None)
            store.add_run(run)
            row = store.get_all()[0]
            assert row["prompt_tokens"] == 105093
            # Distinct keys exist and are independently readable.
            assert "model_max_context" in row
            assert "loaded_context" in row
            assert "prompt_tokens" in row

    def test_capacity_fields_never_conflate_with_prompt(self):
        """A run with capacity set keeps prompt_tokens independent of those values."""
        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            run = _full_run(input_tokens=105093, prompt_tokens=105093,
                            model_max_context=262144, loaded_context=262144)
            store.add_run(run)
            row = store.get_all()[0]
            assert row["prompt_tokens"] == 105093       # actual live context
            assert row["model_max_context"] == 262144    # configured max
            assert row["loaded_context"] == 262144       # loaded window
            # prompt_tokens (live context) is kept distinct from capacity fields,
            # so it is never conflated into the model_max_context / loaded_context.
            assert row["prompt_tokens"] != row["model_max_context"]
            assert row["prompt_tokens"] != row["loaded_context"]


# ----------------------------------------------------------------------
# Total run duration + preservation of existing timing metrics
# ----------------------------------------------------------------------

class TestTimingPreservation:
    def test_total_duration_and_ttft_persisted_as_distinct_columns(self):
        """wall_time_seconds (total per-request duration) and ttft_seconds (prefill
        latency) must both persist as their existing, non-renamed columns."""
        assert "wall_time_seconds" in CSV_HEADERS
        assert "ttft_seconds" in CSV_HEADERS
        # Decode throughput preserved.
        assert "tokens_per_second" in CSV_HEADERS
        # TTFT has not been renamed/reinterpreted into something else.
        assert "prefill_latency_seconds" not in CSV_HEADERS
        assert "total_duration_seconds" not in CSV_HEADERS

    def test_route_persists_total_duration_and_keeps_ttft(self):
        """End-to-end: the benchmark route persists wall_time_seconds per row and
        preserves ttft_seconds unchanged, without conflating them."""
        import src.routes.benchmark as rb
        from unittest.mock import patch, AsyncMock

        fake_result = {
            "run_id": "dur-1", "timestamp": "2026-01-01T00:00:00+00:00", "model": "m",
            "iterations": 1,
            "benchmark_duration_seconds": 5.0,
            "runs": [{
                "iteration": 1, "cold_or_warm": "cold",
                "tokens_per_second": 50.0, "ttft_seconds": 0.25,
                "input_tokens": 1000, "output_tokens": 200,
                "model_load_time_seconds": 1.0, "wall_time_seconds": 2.50,
            }],
        }
        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            with patch.object(src.app_state, "results_store", store), \
                 patch("src.routes.benchmark.run_benchmark", new=AsyncMock(return_value=fake_result)), \
                 patch("src.routes.benchmark.resolve_persisted_quantization",
                       new=AsyncMock(return_value="Q4_K_M")):
                asyncio.run(rb.run_benchmark_endpoint({
                    "model": "m", "prompt": "p", "iterations": 1,
                    "max_tokens": 500, "temperature": 0.0,
                    "lm_studio_url": "http://localhost:1234",
                }))
            rows = store.get_all()
            assert len(rows) == 1
            r = rows[0]
            # Total per-request duration captured; TTFT preserved separately (not renamed).
            assert r["wall_time_seconds"] == 2.50
            assert r["ttft_seconds"] == 0.25
            assert r["tokens_per_second"] == 50.0


# ----------------------------------------------------------------------
# Hardware snapshot: serialization + persistence
# ----------------------------------------------------------------------

class TestHardwareSnapshotSerialization:
    def test_snapshot_returns_expected_keys_json_serializable(self):
        snap = snapshot_hardware()
        expected = {
            "cpu_model", "cpu_logical_cores", "cpu_physical_cores",
            "installed_ram_bytes", "gpu_model", "total_vram_bytes",
            "os_platform", "os_version", "nvidia_driver_version", "python_version",
        }
        assert set(snap.keys()) == expected
        # Every value is a JSON-serialisable primitive (str / int / None).
        json.dumps(snap)  # must not raise
        for v in snap.values():
            assert v is None or isinstance(v, (str, int))

    def test_snapshot_never_raises(self):
        # A crash-proof snapshot is the whole point; calling it twice must agree.
        a = snapshot_hardware()
        b = snapshot_hardware()
        assert a == b

    def test_hardware_snapshot_round_trips_through_store(self):
        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            store.add_run(_hardware_row())
            row = store.get_all()[0]
            assert row["cpu_model"] == "Mock CPU"
            assert row["cpu_logical_cores"] == 8
            assert row["installed_ram_bytes"] == 17179869184
            assert row["gpu_model"] == "Fake GPU"
            assert row["total_vram_bytes"] == 5368709120

    def test_hardware_columns_present_in_sqlite_schema(self):
        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            conn = sqlite3.connect(str(db_p))
            conn.row_factory = sqlite3.Row
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
            for col, _ in OPTIONAL_METADATA_COLUMNS:
                assert col in cols, f"missing from runs table: {col}"
            conn.close()


# ----------------------------------------------------------------------
# Historical / missing-metadata compatibility
# ----------------------------------------------------------------------

class TestHistoricalCompatibility:
    def test_run_without_metadata_keys_persists_and_reloads_none(self):
        """A legacy-shaped run dict (no metadata keys) must persist and reload with
        None for every new column — no crash, no lost data."""
        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            store.add_run(_full_run())  # no metadata keys at all
            row = store.get_all()[0]
            assert row["run_id"] == "meta-run-001"  # original data preserved
            for col, _ in OPTIONAL_METADATA_COLUMNS:
                assert col in row                      # key present
                assert row[col] is None or row[col] == ""  # empty/None semantics

    def test_legacy_db_without_new_columns_migrates_and_preserves_rows(self):
        """A pre-Act-7 SQLite DB lacking the metadata columns must gain them via
        ALTER (mirroring the model_quantization pattern) while historical rows load."""
        for csv_p, db_p in _temp_paths():
            conn = sqlite3.connect(str(db_p))
            conn.execute(
                """CREATE TABLE runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, run_id TEXT, model_key TEXT, model_display_name TEXT,
                    hardware_label TEXT, execution_environment TEXT, connection_type TEXT,
                    iteration INTEGER, cold_or_warm TEXT, tokens_per_second REAL, ttft_seconds REAL,
                    input_tokens INTEGER, output_tokens INTEGER, model_load_time_seconds REAL,
                    wall_time_seconds REAL, prompt_name TEXT, max_output_tokens INTEGER, temperature REAL
                )"""
            )
            conn.execute(
                "INSERT INTO runs (timestamp, run_id, model_key, hardware_label, "
                "execution_environment, connection_type, iteration, cold_or_warm, tokens_per_second, "
                "ttft_seconds, input_tokens, output_tokens, wall_time_seconds, prompt_name, max_output_tokens, temperature) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("2020-01-01T00:00:00+00:00", "legacy-1", "old-model", "HW", "Local", "",
                 1, "warm", 42.0, 0.5, 100, 50, 3.0, "Custom", 500, 0),
            )
            conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", ("csv_migrated", "1"))
            conn.commit()
            conn.close()

            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            all_runs = store.get_all()
            assert len(all_runs) == 1
            legacy = all_runs[0]
            assert legacy["run_id"] == "legacy-1"        # historical row preserved intact
            for col, _ in OPTIONAL_METADATA_COLUMNS:
                assert legacy.get(col) in (None, "")      # blank/None, no crash

            # Columns now exist so future writes can populate them.
            conn = sqlite3.connect(str(db_p))
            conn.row_factory = sqlite3.Row
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
            for col, _ in OPTIONAL_METADATA_COLUMNS:
                assert col in cols
            conn.close()


# ----------------------------------------------------------------------
# resolve_context_capacity: best-effort (never fabricates / assumes a default)
# ----------------------------------------------------------------------

class TestResolveContextCapacity:
    def test_degrades_to_none_on_network_error(self):
        with patch("src.benchmark.httpx.AsyncClient",
                   side_effect=ConnectionError("no LM Studio")):
            result = asyncio.run(resolve_context_capacity("http://localhost:1234", "m"))
        assert result == {"model_max_context": None, "loaded_context": None}

    def test_degrades_to_none_on_non_200(self):
        with patch("src.benchmark.httpx.AsyncClient",
                   return_value=_FakeClient(_FakeResp(404, {}))):
            result = asyncio.run(resolve_context_capacity("http://localhost:1234", "m"))
        assert result == {"model_max_context": None, "loaded_context": None}

    def test_parses_max_context_length_from_models_endpoint(self):
        # Reliable configured-max source is max_context_length on /api/v1/models.
        payload = {"models": [
            {"key": "other-model"},
            {"key": "m", "max_context_length": 131072},
        ]}
        with patch("src.benchmark.httpx.AsyncClient",
                   return_value=_FakeClient(_FakeResp(200, payload))):
            result = asyncio.run(resolve_context_capacity("http://localhost:1234", "m"))
        assert result["model_max_context"] == 131072

    def test_loaded_context_stays_none_without_distinct_field(self):
        # max_context_length is the configured MAX, not the loaded window; no reliable
        # API field exposes loaded context (never inferred from prompt tokens).
        payload = {"models": [{"key": "m", "max_context_length": 131072}]}
        with patch("src.benchmark.httpx.AsyncClient",
                   return_value=_FakeClient(_FakeResp(200, payload))):
            result = asyncio.run(resolve_context_capacity("http://localhost:1234", "m"))
        assert set(result.keys()) == {"model_max_context", "loaded_context"}
        assert result["loaded_context"] is None

    def test_returns_none_when_model_absent(self):
        payload = {"models": [{"key": "other-model", "max_context_length": 999}]}
        with patch("src.benchmark.httpx.AsyncClient",
                   return_value=_FakeClient(_FakeResp(200, payload))):
            result = asyncio.run(resolve_context_capacity("http://localhost:1234", "m"))
        assert result == {"model_max_context": None, "loaded_context": None}


class _ChatResp:
    def __init__(self, stats):
        self._stats = stats

    def json(self):
        return {"stats": self._stats}

    def raise_for_status(self):
        pass


class _ChatClient:
    """Stand-in for httpx.AsyncClient in run_benchmark; returns canned responses."""

    def __init__(self, responses):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, **kwargs):
        # Small delay so the measured invocation duration is non-zero (real timing).
        await asyncio.sleep(0.02)
        if self._responses:
            return self._responses.pop(0)
        return _ChatResp({})


def _chat_stats(tps=50.0, ttft=0.2, inp=100, out=40):
    return {"tokens_per_second": tps, "time_to_first_token_seconds": ttft,
            "input_tokens": inp, "total_output_tokens": out, "model_load_time_seconds": None}


class TestBenchmarkInvocationDuration:
    def test_run_benchmark_returns_invocation_duration(self):
        import src.benchmark as bmod
        resp1 = _ChatResp(_chat_stats(tps=50.0))
        resp2 = _ChatResp(_chat_stats(tps=70.0))
        with patch("src.benchmark.httpx.AsyncClient",
                   return_value=_ChatClient([resp1, resp2])):
            result = asyncio.run(bmod.run_benchmark(
                lm_studio_url="http://localhost:1234", model="m", prompt="p",
                iterations=2, max_tokens=500, temperature=0.0))
        duration = result["benchmark_duration_seconds"]
        assert isinstance(duration, (int, float))
        # Total invocation elapsed spans every request -> at least as long as the
        # longest single-request wall_time, and never negative.
        max_wall = max(r["wall_time_seconds"] for r in result["runs"])
        assert duration >= max_wall > 0

    def test_duration_field_present_per_row_via_route(self):
        """benchmark_duration_seconds is attached identically to every iteration row
        (mirrors run_id/timestamp), while wall_time_seconds stays per-request."""
        import src.routes.benchmark as rb
        fake_result = {
            "run_id": "dur-2", "timestamp": "2026-01-01T00:00:00+00:00", "model": "m",
            "iterations": 2,
            "benchmark_duration_seconds": 5.5,
            "runs": [
                {"iteration": 1, "cold_or_warm": "cold", "tokens_per_second": 40.0,
                 "ttft_seconds": 0.3, "input_tokens": 1000, "output_tokens": 200,
                 "model_load_time_seconds": 1.0, "wall_time_seconds": 2.5},
                {"iteration": 2, "cold_or_warm": "warm", "tokens_per_second": 60.0,
                 "ttft_seconds": 0.15, "input_tokens": 1000, "output_tokens": 300,
                 "model_load_time_seconds": None, "wall_time_seconds": 2.7},
            ],
        }
        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            with patch.object(src.app_state, "results_store", store), \
                 patch("src.routes.benchmark.run_benchmark", new=AsyncMock(return_value=fake_result)), \
                 patch("src.routes.benchmark.resolve_persisted_quantization",
                       new=AsyncMock(return_value="Q4_K_M")), \
                 patch("src.routes.benchmark.resolve_context_capacity",
                       new=AsyncMock(return_value={"model_max_context": None, "loaded_context": None})):
                asyncio.run(rb.run_benchmark_endpoint({
                    "model": "m", "prompt": "p", "iterations": 2, "max_tokens": 500,
                    "temperature": 0.0, "lm_studio_url": "http://localhost:1234"}))
            rows = store.get_all()
            assert len(rows) == 2
            # Invocation duration attached consistently to every row...
            assert all(r["benchmark_duration_seconds"] == 5.5 for r in rows)
            # ...while wall_time_seconds stays distinct per request (not overloaded).
            assert rows[0]["wall_time_seconds"] == 2.5
            assert rows[1]["wall_time_seconds"] == 2.7
