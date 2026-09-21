"""Route-level coverage for the canonical Standard Speed path (Phase 7).

The orchestrator tests exercise the runner and read model directly; these tests prove the
PRODUCTION HTTP surface behaves correctly once wired to the canonical architecture:

* ``POST /api/v2/speed/run`` spawns ``python -m src speed-suite`` (not the legacy
  ``v2-speed``) with a safe argv list.
* ``GET /api/speed/runs/{id}`` serves the canonical evidence shape when rows live in the
  dedicated store, and still serves legacy runs from the shared ResultsStore.
* ``GET /api/v2/speed/runs/{id}/status`` reports ``completed`` for a complete canonical run
  (including partial support) and never reports ``completed`` while points are still missing.

Backend calls that produce canonical rows are monkeypatched to deterministic fakes so no live
LM Studio endpoint, subprocess or disk outside a throwaway tmp dir is touched.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.standard_run_guard as guard
import src.speed_evidence_launch as launch_mod
import src.routes.v2_speed as route_mod
from src.speed_evidence_store import SpeedEvidenceStore


# --------------------------------------------------------------------------- fakes
async def _run_orchestrator(store, monkeypatch, *, max_context=1_000_000):
    """Run the canonical orchestrator against *store* with deterministic backend fakes."""
    import src.speed_evidence_runner as runner
    from src import speed_evidence_gen_runner as gen
    from src import v2_speed_suite_runner as std

    async def _fake_resolve(base_url, model):
        return {
            "quantization": "Q4_K_M",
            "loaded_context": max_context,
            "max_context": max_context,
            "lmstudio_instance_config": {"id": "inst-1"},
        }

    monkeypatch.setattr(runner, "_resolve_suite_identity", _fake_resolve)

    async def _fake_prefill_executor(client, **kwargs):
        fixture = kwargs["fixture"]
        return {
            "run_id": kwargs["run_id"],
            "execution_stage": kwargs["stage"],
            "benchmark_family": "prefill",
            "fixture_id": fixture.fixture_id,
            "fixture_version": str(fixture.fixture_version),
            "target_token_class": fixture.target_token_class,
            "prompt_sha256": fixture.prompt_sha256,
            "input_tokens": fixture.actual_prompt_tokens,
            "total_output_tokens": 32,
            "backend_ttft_seconds": 0.8,
            "prompt_processing_tok_s": 6000.0,
            "backend_generation_tok_s": 150.0,
            "requested_output_tokens": 32,
            "model_instance_id": "inst-1",
        }

    monkeypatch.setattr(runner, "run_speed_execution", _fake_prefill_executor)

    async def _fake_calib(run_id, model, base_url, point):
        return point // 4

    async def _fake_count(run_id, model, base_url, prompt):
        return 8000

    async def _fake_run_benchmark(**kw):
        name = kw.get("prompt_name", "")
        point = {"8K": 8192, "16K": 16384, "32K": 32768}.get(name.split("(")[0].strip(), 8192)
        runs = [
            {
                "iteration": i,
                "cold_or_warm": "cold" if i == 1 else "warm",
                "tokens_per_second": 200.0 if i == 1 else 210.0 + i,
                "ttft_seconds": 0.5 if i == 1 else 0.05,
                "input_tokens": point,
                "output_tokens": 512,
                "wall_time_seconds": 1.0,
                "reasoning_output_tokens": None,
            }
            for i in range(1, 6)
        ]
        return {"runs": runs}

    monkeypatch.setattr(gen, "run_benchmark", _fake_run_benchmark)
    monkeypatch.setattr(gen, "_speed_calibrated_filler_copies", _fake_calib)
    monkeypatch.setattr(std, "_count_input_tokens", _fake_count)
    return await runner.run_canonical_speed_suite(
        "http://localhost:1234", "test-model", store=store
    )


@pytest.fixture
def speed_client():
    from src.main import app

    return TestClient(app)


# --------------------------------------------------------------------------- launch
def test_production_launch_spawns_speed_suite_argv(speed_client, monkeypatch):
    """The production route must spawn ``python -m src speed-suite`` -- never v2-speed."""
    captured = {}

    class _FakeProc:
        def __init__(self, cmd):
            self.captured_cmd = list(cmd)
            self.returncode = 0

    def _fake_popen(cmd, cwd=None, env=None, stdout=None, stderr=None):
        captured["cmd"] = list(cmd)
        return _FakeProc(cmd)

    monkeypatch.setattr(launch_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(guard, "register", lambda run_id, proc: None)

    resp = speed_client.post(
        "/api/v2/speed/run", json={"model": "test-model", "lm_studio_url": "http://localhost:1234"}
    )

    assert resp.status_code == 202
    cmd = captured["cmd"]
    # Canonical child entrypoint, argv list only (no shell string).
    assert "-m" in cmd and "speed-suite" in cmd
    assert "v2-speed" not in cmd
    # run-id is threaded through to the child so status/read can group by it.
    assert "--run-id" in cmd
    run_id = resp.json()["speed_run_id"]
    assert cmd[cmd.index("--run-id") + 1] == run_id


# --------------------------------------------------------------------------- read
def _seed_complete_canonical(monkeypatch, tmp_path):
    store = SpeedEvidenceStore(tmp_path / "canon.db")
    summary = asyncio.run(_run_orchestrator(store, monkeypatch, max_context=1_000_000))
    rid = summary["speed_run_id"]
    rows = store.get_by_run_id(rid)

    def _fake_resolve(run_id):
        return rows if (rows and any(r["run_id"] == run_id for r in rows)) else None

    monkeypatch.setattr(route_mod, "_resolve_canonical_rows", _fake_resolve)
    return rid


def test_http_read_serves_canonical_shape(speed_client, monkeypatch, tmp_path):
    rid = _seed_complete_canonical(monkeypatch, tmp_path)
    body = speed_client.get(f"/api/speed/runs/{rid}").json()

    assert body["run_id"] == rid
    families = {p["benchmark_family"] for p in body["points"]}
    assert families == {"prefill", "generation"}
    gen_point = next(p for p in body["points"] if p["benchmark_family"] == "generation")
    # Generation keeps cold full-prefill + warm_1..warm_4 (5 independent executions).
    assert gen_point["cold_prefill"]["run_count"] == 1
    assert gen_point["cached_warm_reuse"]["run_count"] == 4


def test_http_read_falls_back_to_legacy_when_no_canonical_rows(speed_client, monkeypatch):
    """No canonical rows -> the route must serve the legacy ResultsStore path unchanged."""
    from src.v2_speed_suite_runner import _get_store

    # No canonical executions for this id -> route falls back to the legacy path.
    monkeypatch.setattr(route_mod, "_resolve_canonical_rows", lambda rid: None)

    # Seed a legacy standard run into a temporary shared store (auto-restored).
    from src.results import ResultsStore
    from tests.test_speed_result_routing import standard_run
    import src.app_state as app_state_module

    tmp = Path(tempfile.mkdtemp())
    store = ResultsStore(csv_path=tmp / "a.csv", db_path=tmp / "a.db")
    for r in standard_run("speed-unit-legacy"):
        store.add_run(r)
    monkeypatch.setattr(app_state_module, "results_store", store)

    body = speed_client.get(f"/api/speed/runs/speed-unit-legacy").json()
    # Legacy read model shape (label + configuration), not the canonical fixture shape.
    assert [p["label"] for p in body["points"]] == ["8K", "16K", "32K"]
    assert "configuration" in body


# --------------------------------------------------------------------------- status
def _seed_rows(monkeypatch, tmp_path, *, max_context):
    store = SpeedEvidenceStore(tmp_path / "canon.db")
    summary = asyncio.run(_run_orchestrator(store, monkeypatch, max_context=max_context))
    rid = summary["speed_run_id"]
    rows = store.get_by_run_id(rid)
    monkeypatch.setattr(launch_mod, "_canonical_rows_for", lambda rid: rows)
    return rid, rows


def test_http_status_completed_with_result_url(speed_client, monkeypatch, tmp_path):
    rid, _ = _seed_rows(monkeypatch, tmp_path, max_context=1_000_000)
    body = speed_client.get(f"/api/v2/speed/runs/{rid}/status").json()
    assert body["status"] == "completed"
    assert body["result_url"] == f"/speed/results/{rid}"


def test_http_status_partial_support_completes(speed_client, monkeypatch, tmp_path):
    # Capacity 7000 supports prefill but not generation; unsupported points are terminal, so
    # the suite still completes -- never premature, never failed.
    rid, rows = _seed_rows(monkeypatch, tmp_path, max_context=7000)
    gen_stages = {r["execution_stage"] for r in rows if r["benchmark_family"] == "generation"}
    assert gen_stages == {"unsupported_context"}

    body = speed_client.get(f"/api/v2/speed/runs/{rid}/status").json()
    assert body["status"] == "completed"


def test_http_status_does_not_report_completed_when_incomplete(speed_client, monkeypatch, tmp_path):
    # A run missing an expected point's rows must NOT report completed while in flight.
    rid, _ = _seed_rows(monkeypatch, tmp_path, max_context=1_000_000)
    incomplete = [r for r in SpeedEvidenceStore(tmp_path / "canon.db").get_by_run_id(rid)
                  if not (r.get("target_token_class") == "32K")]
    monkeypatch.setattr(launch_mod, "_canonical_rows_for", lambda rid: incomplete)
    # Child still in flight -> guard reports running, never a premature completed.
    monkeypatch.setattr(guard, "active_ids", lambda: [rid])

    body = speed_client.get(f"/api/v2/speed/runs/{rid}/status").json()
    assert body["status"] != "completed"


# --------------------------------------------------------------------------- affinity
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (32768, 32768),          # real int capacity
        (32768.0, 32768),        # SQLite REAL affinity stores ints as floats -> accepted
        (7000.0, 7000),
        (32768.5, None),         # non-integral float must NOT silently become a capacity
        ("abc", None),           # malformed string rejected
        ("", None),              # empty string rejected
        (None, None),            # missing value rejected
        (True, None),            # bool is an int subclass but is not a capacity
    ],
)
def test_capacity_coercion_accepts_integral_float_and_rejects_malformed(value, expected):
    from src.speed_evidence_launch import _coerce_capacity

    assert _coerce_capacity(value) == expected


def test_http_status_completes_with_sqlite_real_affinity_floats(speed_client, monkeypatch, tmp_path):
    # End-to-end: rows read back from the evidence store carry integral token counts as floats
    # (SQLite REAL affinity). The capacity path must still accept them and report completed.
    rid, _ = _seed_rows(monkeypatch, tmp_path, max_context=1_000_000)
    rows = SpeedEvidenceStore(tmp_path / "canon.db").get_by_run_id(rid)
    loaded = next(r["loaded_context"] for r in rows if r.get("loaded_context") is not None)
    assert isinstance(loaded, float) and loaded.is_integer()  # affinity really produced a float

    body = speed_client.get(f"/api/v2/speed/runs/{rid}/status").json()
    assert body["status"] == "completed"
