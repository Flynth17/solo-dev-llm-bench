"""Focused route + engine tests for the Standard Speed Suite (Act 17).

Exercises launch/status plumbing WITHOUT launching a real benchmark: every
``subprocess.Popen`` call is replaced by an in-memory fake, so no model runtime and no
``python -m src v2-speed`` process is ever spawned. Persistence goes to an in-memory
fake store (monkeypatched), so no SQLite/CSV side effects occur.

Covers the required matrix:

    1. POST valid request            -> 202 + speed_run_id
    2. launch command targets python -m src v2-speed (argv list, no shell)
    3. no call / import of run_v2_quality_live (source + behavioural isolation)
    4. active handle                 -> status running
    5. durable result rows           -> status completed + result_url=/results
    6. process failure               -> status failed (incl. exit code)
    7. second concurrent launch      -> 409 Conflict
    8. missing model                 -> 400
    9. unknown run id                -> 404 ; unsafe run id -> 400
   10. running handle + rows        -> completed wins (transition signal)

Plus engine-level coverage:

   11. canonical points fixed contract (8K / 16K / 32K, iterations, output budget)
   12. prefill throughput derivation correctness + None cases
   13. deterministic content-insensitive context-pressure payload builder
   14. run_speed_suite persists three completed rows with correct metrics
   15. unsupported status persisted when effective/loaded context < target point
   16. model required (ValueError) and legacy-executor behavioural isolation
"""

from __future__ import annotations

import asyncio
import inspect
import re

import pytest
from fastapi.testclient import TestClient

import src.main
import src.v2_speed_suite_runner as vs
import src.routes.v2_speed as vss_route
import src.standard_run_guard as guard


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
    monkeypatch.setattr(vs.subprocess, "Popen", launcher)
    return launcher


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure each test starts (and ends) with empty shared guard state."""
    guard._ACTIVE.clear()
    guard._EXITED.clear()
    yield
    guard._ACTIVE.clear()
    guard._EXITED.clear()


@pytest.fixture(autouse=True)
def _no_speed_logs(tmp_path, monkeypatch):
    """Redirect speed-run log files to a throwaway dir so tests never touch real data/."""
    monkeypatch.setattr(vs, "_speed_runs_root", lambda: tmp_path)
    yield


class FakeStore:
    """Minimal ResultsStore stand-in capturing add_run rows in memory."""

    def __init__(self):
        self.rows = []

    def add_run(self, row):
        self.rows.append(dict(row))

    def get_all(self):
        return list(self.rows)


def _patch_store(monkeypatch, store=None):
    if store is None:
        store = FakeStore()
    monkeypatch.setattr(vs, "_get_store", lambda: store)
    return store


class _FakeResolvers:
    """Return controllable identity/config metadata for run_speed_suite."""

    def __init__(self, model_max_context=None, loaded_context=None, quantization="Q4_K_M"):
        self.model_max_context = model_max_context
        self.loaded_context = loaded_context
        self.quantization = quantization
        # set by the last resolve call, for assertions
        self.resolved_capacity = None

    async def resolve_persisted_quantization(self, base_url, model):
        return self.quantization

    async def resolve_context_capacity(self, base_url, model):
        data = {"model_max_context": self.model_max_context}
        if self.loaded_context is not None:
            data["loaded_context"] = self.loaded_context
        self.resolved_capacity = data
        return data

    async def resolve_loaded_instance_config(self, base_url, model):
        return {
            "loaded_context": self.loaded_context,
            "flash_attention": False,
            "offload_kv_cache_to_gpu": True,
        }


def _bench_for(input_tokens=9000, ttft_seconds=0.12, tps=42.0, n=5):
    """Build a minimal but schema-valid run_benchmark result for one point."""
    runs = [
        {
            "input_tokens": input_tokens,
            "output_tokens": 512 if i == n - 1 else 508,
            "ttft_seconds": ttft_seconds,
            "tokens_per_second": tps,
        }
        for i in range(n)
    ]
    return {
        "runs": runs,
        "warm_aggregate": {"avg_ttft": ttft_seconds, "avg_tokens_per_second": tps},
        "aggregate": {"avg_ttft": ttft_seconds, "avg_tokens_per_second": tps},
        "benchmark_duration_seconds": 12.3,
    }


# ---------------------------------------------------------------------------
# Route: 1. POST valid request -> 202 + speed_run_id
# ---------------------------------------------------------------------------

def test_post_valid_request_returns_202_with_speed_run_id(monkeypatch):
    _make_launch(monkeypatch, exit_code=None)

    resp = client.post("/api/v2/speed/run", json={"model": MODEL, "lm_studio_url": URL})

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "running"
    assert body["speed_run_id"].startswith("speed-")


# ---------------------------------------------------------------------------
# Route: 2. Launch command targets python -m src v2-speed (argv list, no shell)
# ---------------------------------------------------------------------------

def test_launch_uses_locked_v2_speed_argv(monkeypatch):
    launcher = _make_launch(monkeypatch, exit_code=None)

    vs.launch_speed(MODEL, URL)

    assert len(launcher.calls) == 1
    cmd = launcher.calls[0].captured_cmd
    # argv list only -- never a shell string.
    assert isinstance(cmd, list)
    assert "-m" in cmd and "src" in cmd and "v2-speed" in cmd
    assert "--model" in cmd and MODEL in cmd
    assert "--lm-studio-url" in cmd and URL in cmd
    # No legacy executor symbol anywhere in the launched command.
    joined = " ".join(cmd)
    assert "run_v2_quality_live" not in joined
    assert "v2-quality-executor" not in joined


def test_launch_command_is_path_safe_no_shell_injection(monkeypatch):
    launcher = _make_launch(monkeypatch, exit_code=None)

    # A shell metacharacter in the model name must NOT be interpreted by a shell --
    # it is passed as an inert argv element.
    malicious = "$(rm -rf /)"
    vs.launch_speed(malicious, URL)

    cmd = launcher.calls[0].captured_cmd
    assert malicious in cmd  # present verbatim as one argv token (no shell parsing)


# ---------------------------------------------------------------------------
# Route: 3. Isolation from run_v2_quality_live (source + behavioural)
# ---------------------------------------------------------------------------

def test_no_legacy_executor_in_source():
    for mod, name in ((vs, "v2_speed_suite_runner"), (vss_route, "routes.v2_speed")):
        src = inspect.getsource(mod)
        assert ".run_v2_quality_live(" not in src, f"{name} must not call run_v2_quality_live"
        assert not re.search(r"\b(import|from)\b[^\n]*v2_quality_executor", src), \
            f"{name} must not import the legacy executor module"


def test_launch_does_not_invoke_run_v2_quality_live_behaviourally(monkeypatch):
    _make_launch(monkeypatch, exit_code=None)

    import src.v2_quality_executor as legacy

    def _boom(*_a, **_k):
        raise AssertionError("run_v2_quality_live must never be called by the Speed launcher")

    original = legacy.run_v2_quality_live
    legacy.run_v2_quality_live = _boom
    try:
        result = vs.launch_speed(MODEL, URL)
        assert result["status"] == "running"
    finally:
        legacy.run_v2_quality_live = original


# ---------------------------------------------------------------------------
# Route: 4. Active handle -> status running
# ---------------------------------------------------------------------------

def test_active_run_reports_running(monkeypatch):
    _make_launch(monkeypatch, exit_code=None)  # poll() stays None -> still running

    run_id = vs.launch_speed(MODEL, URL)["speed_run_id"]

    code, body = vs.speed_status(run_id)
    assert code == 200
    assert body["status"] == "running"


def test_running_via_http_route(monkeypatch):
    _make_launch(monkeypatch, exit_code=None)
    run_id = vs.launch_speed(MODEL, URL)["speed_run_id"]

    resp = client.get(f"/api/v2/speed/runs/{run_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


# ---------------------------------------------------------------------------
# Route: 5. Durable rows -> completed + result_url=/results
# ---------------------------------------------------------------------------

def test_rows_report_completed_with_result_url(monkeypatch):
    _make_launch(monkeypatch, exit_code=None)
    store = _patch_store(monkeypatch)
    run_id = vs.launch_speed(MODEL, URL)["speed_run_id"]
    # Simulate a finished run that persisted durable rows.
    store.add_run({
        "run_id": run_id, "model_key": MODEL, "speed_point_status": "completed",
        "target_context_tokens": 8192,
    })

    code, body = vs.speed_status(run_id)
    assert code == 200
    assert body["status"] == "completed"
    # Act 19.1: a completed Standard Speed run links to its dedicated page, never the
    # legacy combined /results aggregator.
    assert body["result_url"] == f"/speed/results/{run_id}"


def test_completed_via_http_route(monkeypatch):
    _make_launch(monkeypatch, exit_code=None)
    store = _patch_store(monkeypatch)
    run_id = vs.launch_speed(MODEL, URL)["speed_run_id"]
    store.add_run({"run_id": run_id, "model_key": MODEL})

    resp = client.get(f"/api/v2/speed/runs/{run_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


# ---------------------------------------------------------------------------
# Route: 6. Process failure -> status failed (incl. exit code)
# ---------------------------------------------------------------------------

def test_process_failure_reports_failed(monkeypatch):
    _make_launch(monkeypatch, exit_code=1)
    run_id = vs.launch_speed(MODEL, URL)["speed_run_id"]

    _, body = vs.speed_status(run_id)
    assert body["status"] == "failed"
    assert "exited with status 1" in body["error"]


def test_zero_exit_without_rows_reports_failed(monkeypatch):
    _make_launch(monkeypatch, exit_code=0)  # clean exit but no durable rows
    run_id = vs.launch_speed(MODEL, URL)["speed_run_id"]

    _, body = vs.speed_status(run_id)
    assert body["status"] == "failed"


# ---------------------------------------------------------------------------
# Route: 7. Second concurrent launch -> 409 Conflict
# ---------------------------------------------------------------------------

def test_second_concurrent_launch_returns_409(monkeypatch):
    _make_launch(monkeypatch, exit_code=None)  # first run stays "running"

    client.post("/api/v2/speed/run", json={"model": MODEL, "lm_studio_url": URL})

    resp = client.post("/api/v2/speed/run", json={"model": MODEL, "lm_studio_url": URL})
    assert resp.status_code == 409
    detail = resp.json()["detail"].lower()
    assert "already in progress" in detail


def test_speed_and_workflow_share_the_concurrency_guard(monkeypatch):
    # The cross-suite guard rejects overlap bidirectionally: an active Workflow handle
    # (registered by the Act 16 runner) must block a Speed launch -- and vice versa.
    import src.v2_workflow_runner as wf

    _make_launch(monkeypatch, exit_code=None)
    wf.launch_workflow(MODEL, URL)  # registers into the shared guard

    # Speed enforces the same single-standard-run rule with its own error type; either is
    # a correct bidirectional block (both map to HTTP 409 at the route).
    with pytest.raises((wf.WorkflowLaunchError, vs.SpeedLaunchError)):
        vs.launch_speed(MODEL, URL)

    # And a Speed handle blocks Workflow. Clear BOTH registries so only the shared guard
    # (populated by launch_speed) is what workflow_status consults -- proving cross-suite.
    guard._ACTIVE.clear()
    wf.registry._jobs.clear()
    _make_launch(monkeypatch, exit_code=None)
    vs.launch_speed(MODEL, URL)
    with pytest.raises(wf.WorkflowLaunchError):
        wf.launch_workflow(MODEL, URL)


# ---------------------------------------------------------------------------
# Route: 8. Missing model -> 400 ; 9. unknown/unsafe id
# ---------------------------------------------------------------------------

def test_missing_model_returns_400(monkeypatch):
    launcher = _make_launch(monkeypatch, exit_code=None)

    resp = client.post("/api/v2/speed/run", json={})
    assert resp.status_code == 400
    assert launcher.calls == []  # no process launched for an invalid request


def test_empty_model_returns_400(monkeypatch):
    _make_launch(monkeypatch, exit_code=None)

    resp = client.post("/api/v2/speed/run", json={"model": "   "})
    assert resp.status_code == 400


def test_unknown_run_id_returns_404():
    resp = client.get("/api/v2/speed/runs/speed-doesnotexist/status")
    assert resp.status_code == 404


def test_unsafe_run_id_returns_400():
    # Backslash is a path separator on Windows and must never reach the FS.
    resp = client.get("/api/v2/speed/runs/foo\\bar/status")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Route: 10. Running handle + rows -> completed wins (transition signal)
# ---------------------------------------------------------------------------

def test_running_handle_with_rows_reports_completed(monkeypatch):
    _make_launch(monkeypatch, exit_code=None)
    store = _patch_store(monkeypatch)
    run_id = vs.launch_speed(MODEL, URL)["speed_run_id"]
    store.add_run({"run_id": run_id, "model_key": MODEL})

    code, body = vs.speed_status(run_id)
    assert code == 200
    assert body["status"] == "completed"


# ---------------------------------------------------------------------------
# Engine: 11. Canonical points fixed contract (8K / 16K / 32K + constants)
# ---------------------------------------------------------------------------

def test_canonical_points_are_binary_8k_16k_32k():
    assert vs.CANONICAL_CONTEXT_POINTS == (8192, 16384, 32768)


def test_context_point_labels_match_contract():
    assert vs.CONTEXT_POINT_LABELS[8192] == "8K"
    assert vs.CONTEXT_POINT_LABELS[16384] == "16K"
    assert vs.CONTEXT_POINT_LABELS[32768] == "32K"


def test_fixed_iterations_and_output_budget():
    # Never varies between points or models. Not inherited from the Workflow Suite's
    # 75_percent_context policy -- Speed and Workflow have distinct fixed budgets.
    assert vs.STANDARD_ITERATIONS == 5
    assert vs.STANDARD_OUTPUT_TOKENS == 512


# ---------------------------------------------------------------------------
# Engine: 12. Prefill throughput derivation correctness + None cases
# ---------------------------------------------------------------------------

def test_prefill_throughput_derivation():
    # prefill_tokens_per_second = actual_prompt_tokens / ttft_seconds (authoritative).
    assert vs.prefill_throughput(9000, 0.12) == pytest.approx(75000.0, rel=1e-6)


def test_prefill_throughput_none_when_input_missing():
    assert vs.prefill_throughput(None, 0.12) is None
    assert vs.prefill_throughput(0, 0.12) is None


def test_prefill_throughput_none_when_ttft_missing():
    assert vs.prefill_throughput(9000, None) is None
    assert vs.prefill_throughput(9000, 0.0) is None


# ---------------------------------------------------------------------------
# Engine: 13. Deterministic content-insensitive context-pressure payload builder
# ---------------------------------------------------------------------------

def test_build_context_pressure_prompt_is_deterministic():
    a = vs.build_context_pressure_prompt(8192)
    b = vs.build_context_pressure_prompt(8192)
    assert a == b  # identical across calls (no randomness / no network)


def test_build_context_pressure_prompt_targets_range_and_content_insensitive():
    payload = vs.build_context_pressure_prompt(8192)
    est = src.evaluation_prompts.estimate_tokens(payload)
    # Actual loaded tokens are authoritative from the runtime; a bounded estimation
    # tolerance is expected and never misreported as exact.
    assert 8192 <= est <= 8192 + 512
    # Content-insensitive: no model-specific identifiers or real evaluatable text.
    assert "test-model" not in payload.lower()
    assert "<|end_of_turn|>" not in payload


# ---------------------------------------------------------------------------
# Engine: 14. run_speed_suite persists three completed rows with correct metrics
# ---------------------------------------------------------------------------

def test_run_speed_persists_three_completed_rows_with_metrics(monkeypatch):
    store = _patch_store(monkeypatch)

    resolvers = _FakeResolvers(model_max_context=131072, loaded_context=131072)
    monkeypatch.setattr(vs, "resolve_persisted_quantization", resolvers.resolve_persisted_quantization)
    monkeypatch.setattr(vs, "resolve_context_capacity", resolvers.resolve_context_capacity)
    monkeypatch.setattr(vs, "resolve_loaded_instance_config", resolvers.resolve_loaded_instance_config)

    # run_benchmark returns a bench for whatever point is passed; capture the payloads.
    seen_points = []

    async def _fake_run_benchmark(**kwargs):
        seen_points.append(kwargs.get("prompt"))
        return _bench_for(input_tokens=9001, ttft_seconds=0.13, tps=38.5)

    monkeypatch.setattr(vs, "run_benchmark", _fake_run_benchmark)

    summary = asyncio.run(vs.run_speed_suite(URL, MODEL))

    # All three points supported (capacity huge) -> all completed.
    assert summary["status"] == "completed"
    statuses = [p["status"] for p in summary["points"]]
    assert statuses == ["completed", "completed", "completed"]
    assert {p["target_context_tokens"] for p in summary["points"]} == {8192, 16384, 32768}

    rows = store.rows
    assert len(rows) == 3
    for row in rows:
        assert row["speed_point_status"] == "completed"
        assert row["iteration"] == vs.STANDARD_ITERATIONS
        assert row["temperature"] == 0.0
        assert row["max_output_tokens"] == vs.STANDARD_OUTPUT_TOKENS
        assert row["model_key"] == MODEL
        # Generated throughput flows through the existing tokens_per_second column so the
        # Act 13 read model (and /results) consume it unchanged.
        assert row["tokens_per_second"] == pytest.approx(38.5, rel=1e-6)
        assert row["input_tokens"] == 9001
        # Prefill is derived from the persisted input tokens + TTFT.
        assert row["prefill_tokens_per_second"] == pytest.approx(round(9001 / 0.13, 1), rel=1e-6)
        # Act 20: corrected runs persist a metric version so legacy cached-TTFT prefill on
        # older runs is flagged without rewriting history.
        assert row["speed_metric_version"] == vs.SPEED_METRIC_VERSION
        assert row["speed_metric_version"] == 2


# ---------------------------------------------------------------------------
# Engine: 15. Unsupported status persisted when effective/loaded context < target point
# ---------------------------------------------------------------------------

def test_run_speed_marks_unsupported_when_below_target(monkeypatch):
    store = _patch_store(monkeypatch)

    # Effective capacity (24576) supports 8K and 16K but NOT 32K.
    resolvers = _FakeResolvers(model_max_context=24576, loaded_context=None)
    monkeypatch.setattr(vs, "resolve_persisted_quantization", resolvers.resolve_persisted_quantization)
    monkeypatch.setattr(vs, "resolve_context_capacity", resolvers.resolve_context_capacity)
    monkeypatch.setattr(vs, "resolve_loaded_instance_config", resolvers.resolve_loaded_instance_config)

    async def _fake_run_benchmark(**kwargs):
        return _bench_for(input_tokens=8192 + 30, ttft_seconds=0.11, tps=50.0)

    monkeypatch.setattr(vs, "run_benchmark", _fake_run_benchmark)

    summary = asyncio.run(vs.run_speed_suite(URL, MODEL))

    by_target = {p["target_context_tokens"]: p for p in summary["points"]}
    # 32K below effective capacity -> unsupported, persisted with null metrics.
    assert by_target[32768]["status"] == "unsupported"
    assert by_target[32768]["generation_tokens_per_second"] is None
    # 8K / 16K supported -> completed.
    assert by_target[8192]["status"] == "completed"
    assert by_target[16384]["status"] == "completed"

    unsupported_rows = [r for r in store.rows if r["speed_point_status"] == "unsupported"]
    assert len(unsupported_rows) == 1
    assert unsupported_rows[0]["target_context_tokens"] == 32768


# ---------------------------------------------------------------------------
# Engine: 16. Model required + legacy-executor behavioural isolation (engine level)
# ---------------------------------------------------------------------------

def test_run_speed_requires_model():
    with pytest.raises(ValueError):
        asyncio.run(vs.run_speed_suite(URL, None))
    with pytest.raises(ValueError):
        asyncio.run(vs.run_speed_suite(URL, ""))


def test_run_speed_does_not_invoke_run_v2_quality_live_behaviourally(monkeypatch):
    store = _patch_store(monkeypatch)

    resolvers = _FakeResolvers(model_max_context=131072, loaded_context=131072)
    monkeypatch.setattr(vs, "resolve_persisted_quantization", resolvers.resolve_persisted_quantization)
    monkeypatch.setattr(vs, "resolve_context_capacity", resolvers.resolve_context_capacity)
    monkeypatch.setattr(vs, "resolve_loaded_instance_config", resolvers.resolve_loaded_instance_config)

    async def _fake_run_benchmark(**kwargs):
        return _bench_for(input_tokens=9001, ttft_seconds=0.13, tps=40.0)

    monkeypatch.setattr(vs, "run_benchmark", _fake_run_benchmark)

    import src.v2_quality_executor as legacy

    def _boom(*_a, **_k):
        raise AssertionError("run_v2_quality_live must never be called by the Speed engine")

    original = legacy.run_v2_quality_live
    legacy.run_v2_quality_live = _boom
    try:
        summary = asyncio.run(vs.run_speed_suite(URL, MODEL))
        assert summary["status"] == "completed"
    finally:
        legacy.run_v2_quality_live = original


# ---------------------------------------------------------------------------
# Engine: Act 20 -- authoritative full-prefill sample is cache-busted.
# ---------------------------------------------------------------------------

def test_run_speed_threads_cache_busted_full_prefill_prompt(monkeypatch):
    store = _patch_store(monkeypatch)
    resolvers = _FakeResolvers(model_max_context=131072, loaded_context=131072)
    monkeypatch.setattr(vs, "resolve_persisted_quantization", resolvers.resolve_persisted_quantization)
    monkeypatch.setattr(vs, "resolve_context_capacity", resolvers.resolve_context_capacity)
    monkeypatch.setattr(vs, "resolve_loaded_instance_config", resolvers.resolve_loaded_instance_config)

    captured = {}

    async def _fake_run_benchmark(**kwargs):
        captured["plain"] = kwargs.get("prompt")
        captured["full_prefill"] = kwargs.get("full_prefill_prompt")
        return _bench_for(input_tokens=9001, ttft_seconds=0.13, tps=38.5)

    monkeypatch.setattr(vs, "run_benchmark", _fake_run_benchmark)
    summary = asyncio.run(vs.run_speed_suite(URL, MODEL))

    assert summary["status"] == "completed"
    fp = captured.get("full_prefill")
    plain = captured.get("plain")
    # Iteration 1 (the authoritative full-prefill sample) is cache-busted; iterations 2..5 use
    # the plain payload. The buster is a deterministic prefix at the START of the prompt so it
    # defeats LM Studio's longest-common-prefix KV-cache reuse.
    assert isinstance(fp, str) and isinstance(plain, str)
    assert fp != plain
    assert fp.startswith("[speed-run:")
    assert fp.endswith(plain)  # buster sits before the shared filler
    # And every completed row of a corrected run persists the metric version.
    for row in store.rows:
        assert row.get("speed_point_status") == "completed"
        assert row["speed_metric_version"] == vs.SPEED_METRIC_VERSION
