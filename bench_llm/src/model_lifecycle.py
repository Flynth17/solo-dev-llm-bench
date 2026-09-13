"""LM Studio model-lifecycle adapter (Act 18).

A small, tested wrapper over LM Studio's native v1 REST API that performs explicit
**Load / Unload** of a model instance. It does NO benchmark logic and never imports or
calls the legacy executor -- it only inspects state and issues the two native mutation
endpoints:

    GET  /api/v1/models                  (authoritative state, reused via fetch_models)
    POST /api/v1/models/load   {model}   (+ advisory n_ctx at the standard target)
    POST /api/v1/models/unload {instance_id}

Design constraints honoured:

* **Selection and loading are separate actions.** Nothing here auto-loads a model
  simply because it was selected, and nothing auto-selects a loaded model on the wire.
* **Standard-suite callers may NOT override tuning knobs.** Only the *standard context
  target* is applied to a load (``min(model.max_context_length, 262144)`` when known).
  Context / batch size / flash-attention / MTP / expert count / KV cache / GPU offload
  are never forwarded as overrides -- LM Studio's own defaults and current runtime
  policy are authoritative.
* **The returned loaded configuration is authoritative.** After any mutation we re-read
  ``GET /api/v1/models`` and report what the runtime actually holds; we never claim a
  requested value was applied unless the instance reflects it.
* **No silent reload / eviction.** An already-loaded model is not reloaded; if another
  LLM is already loaded we refuse rather than evicting it (409).

All failures raise :class:`ModelLifecycleError` carrying a concise, human-safe message
plus an HTTP ``status_code`` and a machine ``reason`` so the route layer can map them to
the correct response without ever leaking a stack trace.
"""

from __future__ import annotations

import httpx

# Reuse existing, well-understood machinery rather than reinstate parallel callers.
from src.benchmark import fetch_models, resolve_context_capacity  # noqa: E402

# Native v1 endpoints (LM Studio's Express server; not exposed in the main OpenAPI doc).
MODELS_ENDPOINT = "/api/v1/models"
LOAD_ENDPOINT = "/api/v1/models/load"
UNLOAD_ENDPOINT = "/api/v1/models/unload"

# Standard Solo Dev LLM Bench operating ceiling. A standard load never requests more than
# this, regardless of a model's (possibly larger) configured maximum context window.
STANDARD_MAX_CONTEXT_TOKENS = 262144


class ModelLifecycleError(Exception):
    """Concise, human-safe lifecycle failure mapped to an HTTP status by the route.

    ``status_code`` and ``reason`` are set per error kind so the route can distinguish,
    e.g., ``another_model_loaded`` (409) from ``model_not_found`` (404) without string
    matching on free-form messages. The message is always display-safe (never a trace).
    """

    status_code = 502  # default: generic LM Studio / transport problem
    reason = "lifecycle_failed"

    def __init__(self, message: str, *, status_code=None, reason=None):
        super().__init__(message or "Model lifecycle failed")
        self.message = (message or "Model lifecycle failed").strip()
        if status_code is not None:
            self.status_code = int(status_code)
        if reason is not None:
            self.reason = str(reason)


def standard_load_target(model_max_context):
    """Standard context target for a load: ``min(max, 262144)`` when known & positive.

    Returns an ``int`` at or below :data:`STANDARD_MAX_CONTEXT_TOKENS`, or ``None`` when
    the maximum is unknown so the caller omits ``n_ctx`` rather than fabricating capacity
    or over-requesting a context window the model cannot actually hold.
    """
    if isinstance(model_max_context, int) and not isinstance(model_max_context, bool) \
            and model_max_context > 0:
        return min(model_max_context, STANDARD_MAX_CONTEXT_TOKENS)
    return None


def _quant_label(raw):
    """Render a quantization value into a short display label (mirrors frontend parsing).

    LM Studio may expose quantization as an object (``{"name": "Q5_K_M", ...}``) or an
    already-string label. Returns ``""`` when nothing meaningful is present -- we never
    invent a quantization name.
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for candidate in ("name", "display_name", "quant"):
            val = raw.get(candidate)
            if isinstance(val, str) and val:
                return val
    return ""


def _model_display(model: dict) -> dict:
    """Compact, display-ready snapshot of one model row's metadata.

    Only fields that genuinely exist are included (``None`` when absent -- never
    fabricated). ``context_length`` is the *actual* loaded instance context if present,
    else the configured maximum; both remain authoritative from the runtime.
    """
    key = model.get("key")
    max_ctx = model.get("max_context_length")
    inst_id = None
    loaded_ctx = None
    for inst in (model.get("loaded_instances") or []):
        if isinstance(inst, dict) and inst.get("id"):
            inst_id = inst["id"]
            cfg = inst.get("config") or {}
            ctx = cfg.get("context_length")
            if isinstance(ctx, int) and not isinstance(ctx, bool) and ctx > 0:
                loaded_ctx = ctx
            break

    return {
        "key": key,
        "name": model.get("display_name", model.get("name", key)),
        "loaded": bool(model.get("loaded")),
        "quantization": _quant_label(model.get("quantization")),
        "max_context_length": max_ctx if isinstance(max_ctx, int) else None,
        "instance_id": inst_id,
        # Actual loaded context is authoritative; fall back to configured maximum so the
        # panel always shows *a* context number without pretending a request was applied.
        "context_length": loaded_ctx if isinstance(loaded_ctx, int) else max_ctx
                          if isinstance(max_ctx, int) else None,
    }


def _native_error_message(resp: httpx.Response) -> str:
    """Best-effort, display-safe message from a native LM Studio error response."""
    try:
        body = resp.json()
    except Exception:
        return ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str) and err:
            return err
    text = (resp.text or "").strip()
    return text[:200]


def _map_native_status(code: int) -> int:
    """Map a native HTTP code to our route-facing status.

    ``4xx`` from the runtime propagate as-is; anything else is treated as an LM Studio
    transport/processing problem (502), consistent with :mod:`src.routes.models`.
    """
    if 400 <= code < 500:
        return code
    return 502


async def _native_models(url: str) -> list[dict]:
    """Return the normalized model list from ``GET /api/v1/models`` (reuse fetch_models)."""
    return await fetch_models(url)


async def _get_models_or_conn_error(base: str, reason: str) -> list[dict]:
    """Fetch models, converting a transport failure into a clean 502 lifecycle error.

    Keeps raw httpx errors from leaking to the route/browser; LM Studio unreachable is
    always reported as status_code 502 with a concise message plus the caller's reason.
    """
    try:
        return await _native_models(base)
    except httpx.RequestError as e:
        raise ModelLifecycleError(
            f"Cannot connect to LM Studio at {base}: {e}",
            status_code=502,
            reason=reason,
        )


def _base_url(lm_studio_url) -> str:
    return (lm_studio_url or "").strip().rstrip("/")


async def load_model(
    lm_studio_url: str,
    model_key: str,
    *,
    timeout: float = 30.0,
) -> dict:
    """Load *model_key* at the standard context target and return authoritative state.

    Raises :class:`ModelLifecycleError` (with ``status_code``/``reason``) when the model
    is missing, LM Studio is unreachable, the request is rejected, an identical model is
    already loaded, or a *different* LLM is currently occupying the runtime (409).
    """
    base = _base_url(lm_studio_url)
    key = (model_key or "").strip()
    if not key:
        raise ModelLifecycleError(
            "A model must be selected to load.", status_code=400, reason="no_model"
        )

    # Standard context target is derived purely from discovered metadata -- UI/API callers
    # are never permitted to pass an arbitrary n_ctx / batch / tuning value.
    ctx = await resolve_context_capacity(base, key)
    target = standard_load_target(ctx.get("model_max_context"))

    payload = {"model": key}
    if isinstance(target, int) and target > 0:
        payload["n_ctx"] = target

    existing = await _get_models_or_conn_error(base, "load_failed")

    # Already-loaded requested model -> do NOT reload; report the live state as-is.
    self_model = next(
        (m for m in existing if m.get("key") == key and m.get("loaded")), None
    )
    if self_model is not None:
        return {"action": "noop", **_model_display(self_model)}

    # A *different* heavyweight LLM already loaded -> refuse, never evict (409).
    conflicting = [m for m in existing if m.get("loaded") and m.get("key") != key]
    if conflicting:
        name = _model_display(conflicting[0])["name"] or conflicting[0].get("key")
        raise ModelLifecycleError(
            f"Another LLM is currently loaded ({name}). Unload it before loading this model.",
            status_code=409,
            reason="another_model_loaded",
        )

    # Perform the native load.
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base}{LOAD_ENDPOINT}", json=payload)
    except httpx.RequestError as e:
        raise ModelLifecycleError(
            f"Cannot connect to LM Studio at {base}: {e}",
            status_code=502,
            reason="load_failed",
        )
    if not resp.ok:
            raise ModelLifecycleError(
                _native_error_message(resp) or f"Model load failed (HTTP {resp.status_code})",
                status_code=_map_native_status(resp.status_code),
                reason="load_failed",
            )

    # Authoritative re-read: only report loaded if the runtime actually holds it.
    try:
        post = await _native_models(base)
    except httpx.RequestError:
        post = existing
    model = next((m for m in post if m.get("key") == key), None)
    if not model or not model.get("loaded"):
        raise ModelLifecycleError(
            "Model load request did not result in a loaded state.",
            status_code=502,
            reason="load_failed",
        )
    return {"action": "loaded", **_model_display(model)}


async def unload_model(
    lm_studio_url: str,
    model_key: str,
    *,
    timeout: float = 30.0,
) -> dict:
    """Unload the selected model's *actual* instance id and return the post-unload state.

    Unload always targets the real LM Studio ``instance_id`` -- never assumes
    ``model key == instance id`` (they differ, e.g. ``key`` =
    ``ornith-1.5-35b-a3b`` vs instance id ``atomicchat/ornith-1.5-35b-a3b``).

    * exactly one loaded instance -> unload it;
    * zero loaded instances       -> explicit already-unloaded response (no error);
    * more than one               -> ambiguity error (400), never chosen at random.
    """
    base = _base_url(lm_studio_url)
    key = (model_key or "").strip()
    if not key:
        raise ModelLifecycleError(
            "A model must be selected to unload.", status_code=400, reason="no_model"
        )

    models = await _get_models_or_conn_error(base, "unload_failed")
    model = next((m for m in models if m.get("key") == key), None)
    if model is None:
        raise ModelLifecycleError(
            f"Model '{key}' is not available.", status_code=404, reason="model_not_found"
        )

    instances = [i for i in (model.get("loaded_instances") or []) if isinstance(i, dict)]
    if not instances:
        # Explicit already-unloaded result -- no POST issued, nothing evicted.
        return {"action": "noop", "status": "not_loaded", **_model_display(model)}
    if len(instances) > 1:
        raise ModelLifecycleError(
            "Multiple instances of this model are loaded; cannot disambiguate which to unload.",
            status_code=400,
            reason="ambiguous_multiple_instances",
        )

    instance_id = instances[0].get("id")
    if not instance_id:
        raise ModelLifecycleError(
            "Loaded instance has no usable id for unloading.",
            status_code=502,
            reason="unload_failed",
        )

    # Perform the native unload.
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base}{UNLOAD_ENDPOINT}", json={"instance_id": instance_id}
            )
    except httpx.RequestError as e:
        raise ModelLifecycleError(
            f"Cannot connect to LM Studio at {base}: {e}",
            status_code=502,
            reason="unload_failed",
        )
    if not resp.ok:
            raise ModelLifecycleError(
                _native_error_message(resp) or f"Model unload failed (HTTP {resp.status_code})",
                status_code=_map_native_status(resp.status_code),
                reason="unload_failed",
            )

    # Authoritative re-read to report what the runtime actually holds post-unload.
    try:
        post = await _native_models(base)
    except httpx.RequestError:
        post = models
    model2 = next((m for m in post if m.get("key") == key), None)
    still_loaded = bool(model2 and model2.get("loaded"))
    return {
        "action": "unloaded",
        "model": key,
        "instance_id": instance_id,
        **_model_display(model2 if model2 else {**model, "loaded": False}),
        "status": "not_loaded" if not still_loaded else "loaded",
    }
