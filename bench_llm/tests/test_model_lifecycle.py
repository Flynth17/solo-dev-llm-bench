"""Focused tests for LM Studio model lifecycle (Act 18).

Exercises the load/unload adapter and its HTTP routes WITHOUT any real benchmark run and
WITHOUT touching a live model runtime. Every ``httpx.AsyncClient`` call is replaced by an
in-memory fake that emulates LM Studio's native v1 REST surface:

    GET  /api/v1/models                  (authoritative state)
    POST /api/v1/models/load   {model}   (+ advisory n_ctx at the standard target)
    POST /api/v1/models/unload {instance_id}

The fake tracks loaded-instance state so a load is reflected by later reads and an unload
removes the instance -- exactly how the runtime behaves. No GPU work, no subprocess, no
database side effects.

Covers the required matrix:

Adapter (src.model_lifecycle):
    1. load unloaded model -> action=loaded + authoritative state
    2. standard target = min(max_context, 262144)
    3. max 131072  loads at 131072
    4. max 1048576 loads at 262144
    5. actual returned load config preserved (reads instance, not the request)
    6. already-loaded model -> no duplicate load (no POST)
    7. different LLM already loaded -> 409 (never evicts)
    8. unload uses the real instance id (key != instance id)
    9. zero loaded instances -> explicit already-unloaded result (no POST)
   10. multiple instances -> ambiguity error (400), never random
   11. LM Studio unreachable -> clean 502 (no raw httpx leak)
   12. backend state refreshed after mutation

Route (src.routes.models):
   13. benchmark active -> load 409
   14. benchmark active -> unload 409
   15. another loaded model -> HTTP 409 with reason in the detail
   16. model not found -> HTTP 404
   17. missing model body -> HTTP 400
   18. successful unload shape + status code

Frontend contract (source-level regression guard):
   19-22. endpoints wired; Speed/Workflow gated on loaded; Context stays off; selection
          preserved across refreshes; hardware/url fields not autofill-crossed.
"""

from __future__ import annotations

import asyncio
import json
import os
import re

import httpx
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

import src.main
import src.model_lifecycle as ml
import src.routes.models as models_route
import src.standard_run_guard as guard


MODEL = "test-model-1"
URL = "http://127.0.0.1:1234"

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
client = TestClient(src.main.app)


# ---------------------------------------------------------------------------
# In-memory LM Studio fake -- async context-manager client returning canned v1 payloads.
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload if isinstance(payload, (dict, list)) else {}
        self.status_code = status_code

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def raise_for_status(self):
        # All GET fakes are 200; only POST error responses carry non-2xx, and those paths
        # never call raise_for_status. Kept safe regardless.
        return None

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps({"status_code": self.status_code})


class _FakeClient:
    def __init__(self, ctrl, *args, **kwargs):
        self.ctrl = ctrl

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        if self.ctrl.unreachable:
            raise httpx.ConnectError("simulated connection failure")
        return _FakeResp(self.ctrl.models_payload())

    async def post(self, url, json=None, **kwargs):
        self.ctrl.last_post = {"url": url, "json": json or {}}
        if self.ctrl.unreachable and url.endswith("/load"):
            raise httpx.ConnectError("simulated connection failure")
        return self.ctrl.on_post(url, json or {})


class FakeLMStudio:
    """Mutable stand-in for the LM Studio native v1 model surface."""

    def __init__(self, specs, *, unreachable=False):
        # spec: {display_name, max_context_length, quant, instance_id, instance_config}
        self.specs = {k: dict(v) for k, v in specs.items()}
        # key -> [instance_id, ...]
        self.loaded_ids = {}
        self.last_post = None
        self.load_calls = []
        self.unload_calls = []
        self.unreachable = unreachable

    def seed_loaded(self, key, instance_id):
        self.loaded_ids.setdefault(key, []).append(instance_id)

    def models_payload(self):
        out = []
        for key, spec in self.specs.items():
            insts = [
                {"id": iid, "config": dict(spec.get("instance_config", {}))}
                for iid in self.loaded_ids.get(key, [])
            ]
            out.append({
                "type": "llm",
                "key": key,
                "display_name": spec.get("display_name", key),
                "quantization": spec.get("quant", {"name": "Q4_0"}),
                "max_context_length": spec.get("max_context_length", 262144),
                "loaded_instances": insts,
            })
        return {"models": out}

    def on_post(self, url, json):
        if url.endswith("/load"):
            key = json.get("model")
            # Production LM Studio native v1 REJECTS 'n_ctx' ("Unrecognized key(s): 'n_ctx'").
            # The fake mirrors that so tests prove the adapter no longer sends it (Act 20).
            if "n_ctx" in json:
                return _FakeResp({"error": {"type": "invalid_request_error",
                                            "message": "Unrecognized key(s) in object: 'n_ctx'"}},
                                 status_code=400)
            self.load_calls.append(json)
            if key not in self.specs:
                return _FakeResp({"error": {"type": "model_not_found",
                                            "message": f"{key} not found"}}, status_code=404)
            iid = json.get("instance_id") or self.specs[key].get("instance_id") or key
            self.loaded_ids.setdefault(key, []).append(iid)
            return _FakeResp(self.models_payload())
        if url.endswith("/unload"):
            iid = json.get("instance_id")
            self.unload_calls.append(iid)
            for key in list(self.loaded_ids.keys()):
                if iid in self.loaded_ids[key]:
                    self.loaded_ids[key].remove(iid)
                    break
            return _FakeResp(self.models_payload())
        return _FakeResp({"error": {"message": "unknown endpoint"}}, status_code=400)


# Patch handles installed by ``lm()``; torn down by the autouse fixture below.
_INSTALLED_PATCHES: list = []


def lm(specs, **kwargs):
    """Install the fake httpx client and return its controller for the current test.

    Patching ``httpx.AsyncClient`` on the shared module covers both the GET calls made by
    :func:`src.benchmark.fetch_models` / :func:`resolve_context_capacity` and the POST
    calls made by the lifecycle adapter -- they all reference the same ``httpx`` object.
    Cleanup is handled by :func:`_cleanup_patches` (autouse fixture).
    """
    ctrl = FakeLMStudio(specs, **kwargs)
    patcher = patch.object(httpx, "AsyncClient",
                           lambda *a, **k: _FakeClient(ctrl, *a, **k))
    patcher.start()
    _INSTALLED_PATCHES.append(patcher)
    return ctrl


@pytest.fixture(autouse=True)
def _cleanup_patches():
    yield
    for p in _INSTALLED_PATCHES:
        p.stop()
    _INSTALLED_PATCHES.clear()


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Spec used across adapter tests. Keys deliberately differ from instance ids to prove
# unload targets the real instance id, not the model key.
# ---------------------------------------------------------------------------
SPEC = {
    "ornith-1.5-35b-a3b": {
        "display_name": "Ornith 1.5 35B A3B",
        "max_context_length": 262144,
        "quant": {"name": "Q5_K_M"},
        "instance_id": "atomicchat/ornith-1.5-35b-a3b",
    },
    "qwen-13b": {
        "display_name": "Qwen 13B",
        "max_context_length": 131072,
        "quant": {"name": "Q4_0"},
        "instance_id": "vendor/qwen-13b",
    },
    "big-1m": {
        "display_name": "Big Model 1M",
        "max_context_length": 1048576,
        "quant": {"name": "Q2_K"},
        "instance_id": "vendor/big-1m",
    },
}


# ===========================================================================
# standard_load_target (rule + clamping)
# ===========================================================================
def test_standard_target_equal_to_ceiling():
    assert ml.standard_load_target(262144) == 262144


def test_standard_target_below_ceiling_unchanged():
    # Test 3: a 131072-max model loads at exactly 131072.
    assert ml.standard_load_target(131072) == 131072


def test_standard_target_clamped_to_ceiling():
    # Test 4: a 1048576-max model standard-loads at 262144, never more.
    assert ml.standard_load_target(1048576) == 262144


def test_standard_target_unknown_omitted():
    # Unknown / non-positive maxima omit n_ctx (never fabricate capacity).
    assert ml.standard_load_target(None) is None
    assert ml.standard_load_target(0) is None
    assert ml.standard_load_target(-5) is None


# ===========================================================================
# Adapter: load
# ===========================================================================
def test_load_unloaded_model_returns_loaded_state():
    ctrl = lm(SPEC)
    result = run(ml.load_model(URL, "ornith-1.5-35b-a3b"))

    assert result["action"] == "loaded"
    assert result["loaded"] is True
    # Standard context target applied via 'context_length' (262144 ceiling == model max);
    # production LM Studio rejects the legacy 'n_ctx' key.
    assert ctrl.last_post["json"]["context_length"] == 262144
    assert "n_ctx" not in ctrl.last_post["json"]


def test_load_131072_model_uses_131072():
    ctrl = lm(SPEC)
    run(ml.load_model(URL, "qwen-13b"))
    assert ctrl.last_post["json"]["context_length"] == 131072
    assert "n_ctx" not in ctrl.last_post["json"]


def test_load_1048576_model_clamped_to_262144():
    ctrl = lm(SPEC)
    run(ml.load_model(URL, "big-1m"))
    assert ctrl.last_post["json"]["context_length"] == 262144
    assert "n_ctx" not in ctrl.last_post["json"]


# ---------------------------------------------------------------------------
# Adapter: Act 20 -- native v1 load schema (context_length, no n_ctx)
# ===========================================================================
def test_load_sends_context_length_not_n_ctx():
    ctrl = lm(SPEC)
    run(ml.load_model(URL, "ornith-1.5-35b-a3b"))
    # The supported advisory field is present...
    assert ctrl.last_post["json"]["context_length"] == 262144
    # ...and the rejected legacy key is never sent.
    assert "n_ctx" not in ctrl.last_post["json"]


def test_load_requests_echo_load_config():
    ctrl = lm(SPEC)
    run(ml.load_model(URL, "ornith-1.5-35b-a3b"))
    # Act 20: ask LM Studio to echo back the applied load config for confirmation.
    assert ctrl.last_post["json"].get("echo_load_config") is True


def test_fake_rejects_n_ctx_like_production():
    # The fake mirrors real LM Studio native v1, which rejects 'n_ctx'. Prove the guard so
    # any future regression (re-sending n_ctx) is caught at the adapter boundary.
    ctrl = lm(SPEC)
    resp = ctrl.on_post("http://127.0.0.1:1234/api/v1/models/load",
                        {"model": "ornith-1.5-35b-a3b", "n_ctx": 4096})
    assert not resp.ok
    assert resp.status_code == 400


def test_actual_returned_config_is_authoritative():
    # The instance's real context differs from the requested context_length; we must report
    # actual loaded value, never claim the request was applied.
    spec = dict(SPEC)
    spec["ornith-1.5-35b-a3b"] = {**spec["ornith-1.5-35b-a3b"],
                                  "instance_config": {"context_length": 196608}}
    ctrl = lm(spec)

    run(ml.load_model(URL, "ornith-1.5-35b-a3b"))
    result = run(ml.load_model(URL, "ornith-1.5-35b-a3b"))  # second call re-reads state

    assert result["context_length"] == 196608
    # Only ONE load POST happened (second was a noop re-check) -> no duplicate load.
    assert len(ctrl.load_calls) == 1


def test_already_loaded_model_is_not_reloaded():
    ctrl = lm(SPEC)
    ctrl.seed_loaded("ornith-1.5-35b-a3b", "atomicchat/ornith-1.5-35b-a3b")

    result = run(ml.load_model(URL, "ornith-1.5-35b-a3b"))

    assert result["action"] == "noop"
    assert result["loaded"] is True
    # No POST issued: an already-loaded model is never reloaded.
    assert ctrl.last_post is None


def test_another_llm_loaded_rejects_with_409():
    ctrl = lm(SPEC)
    ctrl.seed_loaded("qwen-13b", "vendor/qwen-13b")

    with pytest.raises(ml.ModelLifecycleError) as ei:
        run(ml.load_model(URL, "big-1m"))

    assert ei.value.status_code == 409
    assert ei.value.reason == "another_model_loaded"
    # Nothing was loaded; the other model is never evicted.
    assert ctrl.last_post is None


def test_load_missing_model_body_raises_400():
    lm(SPEC)
    with pytest.raises(ml.ModelLifecycleError) as ei:
        run(ml.load_model(URL, ""))
    assert ei.value.status_code == 400
    assert ei.value.reason == "no_model"


def test_load_unknown_key_returns_404():
    ctrl = lm(SPEC)
    with pytest.raises(ml.ModelLifecycleError) as ei:
        run(ml.load_model(URL, "does-not-exist"))
    assert ei.value.status_code == 404
    assert ei.value.reason == "load_failed"


def test_lm_studio_unreachable_returns_clean_502():
    ctrl = lm(SPEC, unreachable=True)
    with pytest.raises(ml.ModelLifecycleError) as ei:
        run(ml.load_model(URL, "ornith-1.5-35b-a3b"))
    assert ei.value.status_code == 502
    # A raw httpx error must never leak out of the adapter.
    assert not isinstance(ei.value, httpx.HTTPError)


# ===========================================================================
# Adapter: unload
# ===========================================================================
def test_unload_targets_real_instance_id_not_key():
    ctrl = lm(SPEC)
    run(ml.load_model(URL, "ornith-1.5-35b-a3b"))
    result = run(ml.unload_model(URL, "ornith-1.5-35b-a3b"))

    url = ctrl.last_post["url"]
    assert url.endswith("/api/v1/models/unload")
    # Instance id (publisher-prefixed) -- NOT the model key.
    assert ctrl.last_post["json"]["instance_id"] == "atomicchat/ornith-1.5-35b-a3b"
    assert result["action"] == "unloaded"
    assert result["status"] == "not_loaded"


def test_unload_zero_instances_is_explicit_result():
    ctrl = lm(SPEC)
    result = run(ml.unload_model(URL, "qwen-13b"))
    # No POST; explicit already-unloaded response.
    assert ctrl.last_post is None
    assert result["action"] == "noop"
    assert result["status"] == "not_loaded"


def test_unload_multiple_instances_is_ambiguity_error():
    ctrl = lm(SPEC)
    ctrl.seed_loaded("ornith-1.5-35b-a3b", "id-A")
    ctrl.seed_loaded("ornith-1.5-35b-a3b", "id-B")
    with pytest.raises(ml.ModelLifecycleError) as ei:
        run(ml.unload_model(URL, "ornith-1.5-35b-a3b"))
    assert ei.value.status_code == 400
    assert ei.value.reason == "ambiguous_multiple_instances"
    assert ctrl.last_post is None


def test_unload_unknown_key_returns_404():
    lm(SPEC)
    with pytest.raises(ml.ModelLifecycleError) as ei:
        run(ml.unload_model(URL, "nope"))
    assert ei.value.status_code == 404
    assert ei.value.reason == "model_not_found"


def test_backend_state_refreshed_after_mutation():
    ctrl = lm(SPEC)
    run(ml.load_model(URL, "qwen-13b"))
    # A fresh read after the load reflects the new loaded state (authoritative re-read).
    from src.benchmark import fetch_models
    models = run(fetch_models(URL))
    qwen = next(m for m in models if m["key"] == "qwen-13b")
    assert qwen["loaded"] is True
    assert len(qwen["loaded_instances"]) == 1


# ===========================================================================
# Route layer (HTTP status mapping + guards)
# ===========================================================================
def test_route_load_missing_model_returns_400(monkeypatch):
    monkeypatch.setattr(models_route, "lifecycle_load", lambda *a, **k: None)
    resp = client.post("/api/models/load", json={"lm_studio_url": URL})
    assert resp.status_code == 400


def test_route_unload_missing_model_returns_400(monkeypatch):
    monkeypatch.setattr(models_route, "lifecycle_unload", lambda *a, **k: None)
    resp = client.post("/api/models/unload", json={"lm_studio_url": URL})
    assert resp.status_code == 400


def test_route_benchmark_active_rejects_load(monkeypatch):
    monkeypatch.setattr(guard, "is_standard_run_active", lambda exclude=None: "speed-abc")
    monkeypatch.setattr(models_route, "lifecycle_load",
                        lambda *a, **k: {"action": "loaded"})
    resp = client.post("/api/models/load", json={"model": MODEL, "lm_studio_url": URL})
    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"].lower()


def test_route_benchmark_active_rejects_unload(monkeypatch):
    monkeypatch.setattr(guard, "is_standard_run_active", lambda exclude=None: "speed-abc")
    monkeypatch.setattr(models_route, "lifecycle_unload",
                        lambda *a, **k: {"action": "unloaded"})
    resp = client.post("/api/models/unload", json={"model": MODEL, "lm_studio_url": URL})
    assert resp.status_code == 409


def test_route_another_model_loaded_maps_409(monkeypatch):
    def _boom(*a, **k):
        raise ml.ModelLifecycleError(
            "Another LLM is currently loaded. Unload it before loading this model.",
            status_code=409, reason="another_model_loaded",
        )
    monkeypatch.setattr(models_route, "lifecycle_load", _boom)
    resp = client.post("/api/models/load", json={"model": MODEL, "lm_studio_url": URL})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "another_model_loaded" in detail


def test_route_model_not_found_maps_404(monkeypatch):
    def _boom(*a, **k):
        raise ml.ModelLifecycleError("Model missing", status_code=404, reason="model_not_found")
    monkeypatch.setattr(models_route, "lifecycle_load", _boom)
    resp = client.post("/api/models/load", json={"model": MODEL, "lm_studio_url": URL})
    assert resp.status_code == 404


def test_route_successful_unload_shape(monkeypatch):
    async def _ok(*a, **k):
        return {"action": "unloaded", "model": MODEL, "instance_id": "vendor/x",
                "status": "not_loaded"}
    monkeypatch.setattr(models_route, "lifecycle_unload", _ok)
    resp = client.post("/api/models/unload", json={"model": MODEL, "lm_studio_url": URL})
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "unloaded"
    assert body["instance_id"] == "vendor/x"


def test_route_load_forbids_arbitrary_n_ctx_override(monkeypatch):
    # UI/API callers must NOT be able to pass an arbitrary n_ctx / batch / tuning value.
    captured = {}

    async def _capture(base_url, model_key, **kw):
        captured.update(kw)
        return {"action": "loaded", "loaded": True}

    monkeypatch.setattr(models_route, "lifecycle_load", _capture)
    resp = client.post("/api/models/load", json={
        "model": MODEL, "lm_studio_url": URL,
        "n_ctx": 999999, "eval_batch_size": 512, "flash_attn": True,
    })
    assert resp.status_code == 200
    # The adapter never received any override knobs -- only model + context target.
    assert "n_ctx" not in captured or captured.get("n_ctx") is None
    for knob in ("eval_batch_size", "flash_attn"):
        assert knob not in captured


# ===========================================================================
# Frontend contract (source-level regression guard for gating / wiring)
# ===========================================================================
def _read_js():
    with open(os.path.join(APP_DIR, "dashboard.js"), encoding="utf-8") as fh:
        return fh.read()


def _read_html():
    with open(os.path.join(APP_DIR, "index.html"), encoding="utf-8") as fh:
        return fh.read()


def test_frontend_wires_load_and_unload_endpoints():
    js = _read_js()
    # The dashboard builds the two endpoints from a shared base + action token.
    assert '"/api/models/"' in js
    assert '"load"' in js and '"unload"' in js


def test_frontend_gates_speed_and_workflow_on_loaded():
    js = _read_js()
    # Suites are disabled unless the selected model is loaded.
    assert re.search(r"setSuiteGating\(!!\(\w+ && \w+\.loaded\)\)", js) or \
           "setSuiteGating(!!(" in js
    assert "runSpeedBtn.disabled = !enabled" in js
    assert "runWorkflowBtn.disabled = !enabled" in js


def test_frontend_context_stays_disabled():
    html = _read_html()
    m = re.search(r'id="run-context"[^>]*>', html)
    assert m, "run-context button must still be present"
    assert "disabled" in m.group(0), "Context remains Planned/disabled"


def test_frontend_preserves_selection_and_has_state_controls():
    js = _read_js()
    assert "_prevSelection" in js and "model-state-line" in js
    assert 'load-unload-btn' in js
    html = _read_html()
    assert 'id="model-state-line"' in html
    assert 'id="load-unload-btn"' in html


def test_frontend_hardware_and_url_fields_not_autofill_crossed():
    html = _read_html()
    # Minimal HTML mitigation for browser autofill cross-contamination (Act 17.1 finding).
    m = re.search(r'id="lm-studio-url"[^>]*>', html)
    h = re.search(r'id="hardware-label"[^>]*>', html)
    assert m and "autocomplete" in m.group(0)
    assert h and "autocomplete" in h.group(0)


def test_no_duplicate_global_result_link_helper():
    # Act 17.1 FIX: no shared makeResultLink; distinct speed/workflow helpers.
    js = _read_js()
    defs = re.findall(r'function (\w*ResultLink)\(', js)
    assert "makeSpeedResultLink" in defs and "makeWorkflowResultLink" in defs
    # Exactly one definition of each (no shadowing duplicate).
    assert defs.count("makeSpeedResultLink") == 1
    assert defs.count("makeWorkflowResultLink") == 1
