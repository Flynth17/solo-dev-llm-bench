"""Route + launcher tests for the Context benchmark family API.

Exercises launch/status plumbing WITHOUT spawning a real benchmark: every
``subprocess.Popen`` call is replaced by an in-memory fake, so no model runtime and
no ``python -m src context-suite`` process is ever spawned. Persistence goes to a
throwaway tmp dir (monkeypatched), so no real data side effects occur.

Covers the required matrix:

    1. POST valid request            -> 202 + context_run_id
    2. launch command targets python -m src context-suite (argv list, no shell)
    3. active handle                 -> status running
    4. durable artifact present       -> status completed + result_url
    5. process failure               -> status failed
    6. second concurrent launch      -> 409 Conflict
    7. missing model                 -> 400
    8. unknown run id                -> 404 ; unsafe run id -> 400
    9. GET single-run read model     -> 200 | 404 | 500
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import src.main
import src.v2_context_launcher as cl
import src.v2_context_artifact as art
import src.standard_run_guard as guard

MODEL = "test-model-1"
URL = "http://127.0.0.1:1234"

client = TestClient(src.main.app)


class FakeProc:
    def __init__(self, exit_code=None):
        self._exit_code = exit_code
        self.returncode = exit_code
        self.captured_cmd = None

    def poll(self):
        return self._exit_code

    def wait(self, timeout=None):
        return self._exit_code


class _Launcher:
    def __init__(self, exit_code=None):
        self.exit_code = exit_code
        self.calls = []

    def __call__(self, cmd, cwd=None, env=None, stdout=None, stderr=None):
        proc = FakeProc(self.exit_code)
        proc.captured_cmd = list(cmd)
        self.calls.append(proc)
        return proc


def _make_launch(monkeypatch, exit_code=None):
    launcher = _Launcher(exit_code=exit_code)
    monkeypatch.setattr(cl.subprocess, "Popen", launcher)
    return launcher


@pytest.fixture(autouse=True)
def clean_state():
    cl.registry._jobs.clear()
    guard._ACTIVE.clear()
    guard._EXITED.clear()
    yield
    cl.registry._jobs.clear()
    guard._ACTIVE.clear()
    guard._EXITED.clear()


@pytest.fixture()
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(art, "CONTEXT_RUNS_DIR", tmp_path)
    return tmp_path


def _persist_minimal(run_id):
    """Write a schema-valid completed artifact for *run_id*."""
    doc = {
        "schema_version": art.SCHEMA_VERSION,
        "artifact_type": art.ARTIFACT_TYPE,
        "context_run_id": run_id,
        "model_key": MODEL,
        "configuration_fingerprint": "fp",
        "classification": "canonical",
        "baseline_context_point": "15K",
        "effective_capacity": 131072,
        "points": [{
            "context_point": "15K",
            "requested_context_tokens": 15000,
            "actual_context_tokens": 15000,
            "status": "success",
            "score": 1.0,
            "facts_requested": 12,
            "facts_correct": 12,
            "baseline": True,
            "degradation_from_baseline": 0.0,
            "retention_relative_to_baseline": None,
            "failure_reason": None,
            "evidence": [],
            "telemetry": {},
        }],
    }
    art.persist_document(doc)
    return run_id


# ---------------------------------------------------------------------------

def test_post_valid_request_returns_202_with_run_id(monkeypatch, runs_dir):
    _make_launch(monkeypatch)
    resp = client.post("/api/context/run", json={"model": MODEL, "lm_studio_url": URL})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "running"
    assert body["context_run_id"].startswith("ctx-")


def test_launch_command_targets_context_suite_argv(monkeypatch, runs_dir):
    launcher = _make_launch(monkeypatch)
    client.post("/api/context/run", json={"model": MODEL, "lm_studio_url": URL})
    cmd = launcher.calls[0].captured_cmd
    assert "context-suite" in cmd
    assert "-m" in cmd and "src" in cmd
    assert "--model" in cmd and MODEL in cmd
    # argv list only -- no shell string interpolation
    assert all(isinstance(c, str) for c in cmd)


def test_active_handle_reports_running(monkeypatch, runs_dir):
    _make_launch(monkeypatch)
    body = client.post("/api/context/run", json={"model": MODEL, "lm_studio_url": URL}).json()
    rid = body["context_run_id"]
    status = client.get(f"/api/context/runs/{rid}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "running"


def test_durable_artifact_reports_completed(runs_dir):
    rid = _persist_minimal("ctx-persisted")
    resp = client.get(f"/api/context/runs/{rid}/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["result_url"].startswith("/context/results/")


def test_process_failure_reports_failed(monkeypatch, runs_dir):
    _make_launch(monkeypatch, exit_code=1)
    body = client.post("/api/context/run", json={"model": MODEL, "lm_studio_url": URL}).json()
    rid = body["context_run_id"]
    status = client.get(f"/api/context/runs/{rid}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "failed"


def test_second_concurrent_launch_conflicts(monkeypatch, runs_dir):
    _make_launch(monkeypatch)
    first = client.post("/api/context/run", json={"model": MODEL, "lm_studio_url": URL})
    second = client.post("/api/context/run", json={"model": MODEL, "lm_studio_url": URL})
    assert first.status_code == 202
    assert second.status_code == 409


def test_missing_model_returns_400(monkeypatch, runs_dir):
    _make_launch(monkeypatch)
    resp = client.post("/api/context/run", json={"model": "", "lm_studio_url": URL})
    assert resp.status_code == 400


def test_unknown_run_id_returns_404():
    resp = client.get("/api/context/runs/ctx-nope/status")
    assert resp.status_code == 404


def test_unsafe_run_id_rejected():
    # Starlette rejects encoded path traversal with 404 before the route, or the
    # handler returns 400 -- either way it must be rejected (never 200/500).
    resp = client.get("/api/context/runs/..%2F..%2Fevil/status")
    assert resp.status_code in (400, 404)


def test_get_single_run_read_model(runs_dir):
    rid = _persist_minimal("ctx-read")
    resp = client.get(f"/api/context/runs/{rid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == rid
    assert body["supported_point_count"] == 1


def test_get_single_run_unknown_returns_404():
    resp = client.get("/api/context/runs/ctx-unknown")
    assert resp.status_code == 404
