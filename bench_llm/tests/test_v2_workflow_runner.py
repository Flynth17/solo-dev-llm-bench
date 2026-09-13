"""Focused route/job tests for the Workflow suite launcher (Act 16).

Exercises the launch + status plumbing WITHOUT launching a real benchmark: every
``subprocess.Popen`` call is replaced by an in-memory fake, so no model runtime and
no ``python -m src v2-run`` process is ever spawned. Completion is proven against a
throwaway artifact directory (same convention as the existing V2 route tests).

Covers the required matrix:

    1. POST valid request        -> 202 + run_id
    2. launch command targets the locked ``python -m src v2-run`` architecture
    3. no call / import of ``run_v2_quality_live`` (source + behavioural isolation)
    4. active handle             -> status running
    5. valid persisted artifact  -> status completed + result URL
    6. process failure           -> status failed (incl. exit code 2 -> mismatch)
    7. second concurrent launch  -> 409 Conflict
    8. missing model             -> 400
    9. unknown run id            -> 404 ; unsafe run id -> 400
   10. running handle + artifact -> completed wins (transition signal)
"""

from __future__ import annotations

import inspect
import re

import pytest
from fastapi.testclient import TestClient

import src.main
import src.v2_quality_artifact as art
import src.v2_workflow_runner as wf
import src.routes.v2_workflow as wf_route
from src.v2_quality_artifact import persist_document
from src.v2_workflow_runner import launch_workflow, workflow_status, WorkflowLaunchError


RUN_ID = "run-abcdef012345"
MODEL = "test-model-1"
URL = "http://127.0.0.1:1234"

client = TestClient(src.main.app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeProc:
    """Stand-in for ``subprocess.Popen`` with a controllable exit state."""

    def __init__(self, exit_code=None):
        self._exit_code = exit_code
        self.returncode = exit_code
        self.captured_cmd = None

    def poll(self):
        return self._exit_code

    def wait(self, timeout=None):
        return self._exit_code


class _Launcher:
    """Capture the argv passed to Popen and drive launch with a fake process."""

    def __init__(self, exit_code=None):
        self.exit_code = exit_code
        self.calls = []

    def __call__(self, cmd, cwd=None, env=None, stdout=None, stderr=None):
        proc = FakeProc(self.exit_code)
        proc.captured_cmd = list(cmd)
        self.calls.append(proc)
        return proc


def _make_launch(monkeypatch, exit_code=None):
    """Patch subprocess.Popen in the runner module with a capturing fake."""
    launcher = _Launcher(exit_code=exit_code)
    monkeypatch.setattr(wf.subprocess, "Popen", launcher)
    return launcher


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure each test starts from an empty in-memory process registry."""
    wf.registry._jobs.clear()
    yield
    wf.registry._jobs.clear()


@pytest.fixture()
def v2_runs_dir(tmp_path, monkeypatch):
    """Point the artifact store at a throwaway directory for the duration of a test."""
    monkeypatch.setattr(art, "V2_RUNS_DIR", tmp_path)
    return tmp_path


def _persist_artifact(v2_runs_dir, run_id=RUN_ID):
    """Write a minimal but schema-valid completed artifact for *run_id*."""
    doc = {
        "schema_version": art.SCHEMA_VERSION,
        "artifact_type": art.ARTIFACT_TYPE,
        "run_id": run_id,
        "aggregate": {"checks_total": 166},
    }
    persist_document(doc)
    return run_id


# ---------------------------------------------------------------------------
# 1. POST valid request -> 202 + run_id
# ---------------------------------------------------------------------------

def test_post_valid_request_returns_202_with_run_id(monkeypatch, v2_runs_dir):
    _make_launch(monkeypatch, exit_code=None)

    resp = client.post("/api/v2/workflow/run", json={"model": MODEL, "lm_studio_url": URL})

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "running"
    assert body["run_id"].startswith("run-")


# ---------------------------------------------------------------------------
# 2. Launch command targets the locked v2-run architecture (argv list, no shell)
# ---------------------------------------------------------------------------

def test_launch_uses_locked_v2_run_argv(monkeypatch, v2_runs_dir):
    launcher = _make_launch(monkeypatch, exit_code=None)

    launch_workflow(MODEL, URL)

    assert len(launcher.calls) == 1
    cmd = launcher.calls[0].captured_cmd
    # argv list only -- never a shell string.
    assert isinstance(cmd, list)
    assert "-m" in cmd and "src" in cmd and "v2-run" in cmd
    assert "--model" in cmd and MODEL in cmd
    assert "--lm-studio-url" in cmd and URL in cmd
    # No legacy executor symbol anywhere in the launched command.
    assert "v2-quality-executor" not in " ".join(cmd)
    assert "run_v2_quality_live" not in " ".join(cmd)


# ---------------------------------------------------------------------------
# 3. Isolation from run_v2_quality_live (source + behavioural)
# ---------------------------------------------------------------------------

def test_no_legacy_executor_in_source():
    for mod, name in ((wf, "v2_workflow_runner"), (wf_route, "routes.v2_workflow")):
        src = inspect.getsource(mod)
        assert ".run_v2_quality_live(" not in src, f"{name} must not call run_v2_quality_live"
        assert not re.search(r"\b(import|from)\b[^\n]*v2_quality_executor", src), \
            f"{name} must not import the legacy executor module"


def test_launch_does_not_invoke_run_v2_quality_live_behaviourally(monkeypatch, v2_runs_dir):
    _make_launch(monkeypatch, exit_code=None)

    import src.v2_quality_executor as legacy

    def _boom(*_a, **_k):
        raise AssertionError("run_v2_quality_live must never be called by the Workflow launcher")

    original = legacy.run_v2_quality_live
    legacy.run_v2_quality_live = _boom
    try:
        result = launch_workflow(MODEL, URL)
        assert result["status"] == "running"
    finally:
        legacy.run_v2_quality_live = original


# ---------------------------------------------------------------------------
# 4. Active handle -> status running
# ---------------------------------------------------------------------------

def test_active_run_reports_running(monkeypatch, v2_runs_dir):
    _make_launch(monkeypatch, exit_code=None)  # poll() stays None -> still running

    run_id = launch_workflow(MODEL, URL)["run_id"]

    assert workflow_status(run_id) == (200, {"run_id": run_id, "status": "running"})


# ---------------------------------------------------------------------------
# 5. Valid persisted artifact -> completed + result URL
# ---------------------------------------------------------------------------

def test_artifact_reports_completed_with_result_url(v2_runs_dir):
    _persist_artifact(v2_runs_dir, RUN_ID)

    status_code, body = workflow_status(RUN_ID)

    assert status_code == 200
    assert body["status"] == "completed"
    assert body["run_id"] == RUN_ID
    assert body["result_url"] == f"/v2/results/{RUN_ID}"


def test_artifact_via_http_route(v2_runs_dir):
    _persist_artifact(v2_runs_dir, RUN_ID)

    resp = client.get(f"/api/v2/workflow/runs/{RUN_ID}/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["result_url"] == f"/v2/results/{RUN_ID}"


# ---------------------------------------------------------------------------
# 6. Process failure -> status failed (incl. exit code 2 -> mismatch)
# ---------------------------------------------------------------------------

def test_process_failure_reports_failed(monkeypatch, v2_runs_dir):
    _make_launch(monkeypatch, exit_code=1)

    run_id = launch_workflow(MODEL, URL)["run_id"]

    status_code, body = workflow_status(run_id)
    assert status_code == 200
    assert body["status"] == "failed"
    assert "exited with status 1" in body["error"]


def test_exit_code_2_reports_configuration_mismatch(monkeypatch, v2_runs_dir):
    _make_launch(monkeypatch, exit_code=2)

    run_id = launch_workflow(MODEL, URL)["run_id"]

    _, body = workflow_status(run_id)
    assert body["status"] == "failed"
    assert "configuration mismatch" in body["error"].lower()


def test_zero_exit_without_artifact_reports_failed(monkeypatch, v2_runs_dir):
    _make_launch(monkeypatch, exit_code=0)  # clean exit but no artifact written

    run_id = launch_workflow(MODEL, URL)["run_id"]

    _, body = workflow_status(run_id)
    assert body["status"] == "failed"


# ---------------------------------------------------------------------------
# 7. Second concurrent Workflow launch -> 409 Conflict
# ---------------------------------------------------------------------------

def test_second_concurrent_launch_returns_409(monkeypatch, v2_runs_dir):
    _make_launch(monkeypatch, exit_code=None)  # first run stays "running"

    client.post("/api/v2/workflow/run", json={"model": MODEL, "lm_studio_url": URL})

    resp = client.post("/api/v2/workflow/run", json={"model": MODEL, "lm_studio_url": URL})
    assert resp.status_code == 409
    detail = resp.json()["detail"].lower()
    assert "already in progress" in detail


# ---------------------------------------------------------------------------
# 8. Missing model -> 400
# ---------------------------------------------------------------------------

def test_missing_model_returns_400(monkeypatch, v2_runs_dir):
    launcher = _make_launch(monkeypatch, exit_code=None)

    resp = client.post("/api/v2/workflow/run", json={})
    assert resp.status_code == 400
    # No process was launched for an invalid request.
    assert launcher.calls == []


def test_empty_model_returns_400(monkeypatch, v2_runs_dir):
    _make_launch(monkeypatch, exit_code=None)

    resp = client.post("/api/v2/workflow/run", json={"model": "   "})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 9. Unknown run id -> 404 ; unsafe run id -> 400
# ---------------------------------------------------------------------------

def test_unknown_run_id_returns_404(v2_runs_dir):
    resp = client.get("/api/v2/workflow/runs/run-doesnotexist/status")
    assert resp.status_code == 404


def test_unsafe_run_id_returns_400(v2_runs_dir):
    # Backslash is a path separator on Windows and must never reach the FS.
    resp = client.get("/api/v2/workflow/runs/foo\\bar/status")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 10. Running handle + durable artifact -> completed wins (transition signal)
# ---------------------------------------------------------------------------

def test_running_handle_with_artifact_reports_completed(monkeypatch, v2_runs_dir):
    # A finished artifact is strong evidence of completion even if the in-memory
    # process handle still looks alive (e.g. after a FastAPI restart race).
    _make_launch(monkeypatch, exit_code=None)
    run_id = launch_workflow(MODEL, URL)["run_id"]
    _persist_artifact(v2_runs_dir, run_id)

    status_code, body = workflow_status(run_id)
    assert status_code == 200
    assert body["status"] == "completed"
    assert body["result_url"] == f"/v2/results/{run_id}"


# ---------------------------------------------------------------------------
# Concurrency guard at the engine level (not just the route).
# ---------------------------------------------------------------------------

def test_engine_concurrent_launch_raises(monkeypatch, v2_runs_dir):
    _make_launch(monkeypatch, exit_code=None)

    launch_workflow(MODEL, URL)
    with pytest.raises(WorkflowLaunchError):
        launch_workflow(MODEL, URL)
