# Act 18 — LM Studio Model Lifecycle (Load / Unload)

**Branch:** `feat/benchmark-v2` · **Checkpoint:** `347b8ed feat: clean launcher for standard suites (Act 17.1)`

Adds explicit model **Load / Unload** management to the cleaned standard-suite launcher.
Selection and loading remain *separate* actions — selecting a model never loads it, so the
heavyweight suite-run guard is untouched by mere selection. Benchmark mechanics are unchanged.

## Requirement → Coverage Trace

| # | Requirement | Where | Verified by |
|---|-------------|-------|-------------|
| 1 | Select / inspect state | `static/index.html` + `dashboard.js` `_syncModelControls()`, `renderModels()` | UI (#15) |
| 2 | Selection is separate from load; no auto-load on selection | `loadModels()` preserves `_prevSelection`; never posts `/load` | Test #6, #19 |
| 3 | Load button (blue, left of dropdown) | `static/index.html` `#load-unload-btn`, `.btn-blue` | UI (#15) |
| 4 | Unload button (grey, below dropdown), only when loaded | `_syncModelControls()` toggles display | UI (#16) |
| 5 | Native REST: GET `/api/v1/models`, POST `/load {model}` (+ advisory `n_ctx`), POST `/unload {instance_id}` | `src/model_lifecycle.py` | Test #8, #9 |
| 6 | Load only one model; reject another-loaded with reason (409) | `lifecycle_load()` → 409 `another_model_loaded`; never evicts | Tests #7, #15 |
| 7 | Standard context target = min(max_context, 262144) | `standard_load_target()`; posts advisory `n_ctx` only | Tests #3, #4 (load_131072 / load_1048576) |
| 8 | Actual returned config authoritative (from instance, not request) | `resolve_context_capacity()` reads live GET after load | Test #5 (`test_actual_returned_config_is_authoritative`) |
| 9 | Unload targets real instance id, not model key | `_find_instance_id_for_unload()`, posts `{instance_id}` | Test #8 (`unload_targets_real_instance_id_not_key`) |
| 10 | Multiple instances → ambiguity error (400), never random | `lifecycle_unload()` → 400 `ambiguous_multiple_instances` | Test #10 |
| 11 | Zero loaded → explicit already-unloaded result, no eviction, no POST | `unload_model()` → `{action:noop,status:not_loaded}` | Test #9 (`unload_zero_instances_is_explicit_result`) |
| 12 | Backend state refreshed after mutation (re-read GET) | adapter calls `_native_models()` again before returning | Test #13 (`test_backend_state_refreshed_after_mutation`) |
| 13 | No benchmark running → load/unload allowed | routes consult `src.standard_run_guard.is_standard_run_active` | Tests #13, #14 (route_benchmark_active) |
| 14 | Benchmark active → reject load AND unload with 409 | both routes call `_reject_if_benchmark_active()` | Tests #13, #14 |
| 15 | Model not found → clean 404; LM Studio unreachable → clean 502 (no raw httpx leak) | `ModelLifecycleError` mapping in routes | Tests `load_unknown_key_returns_404`, `lm_studio_unreachable_returns_clean_502` |
| 16 | Speed & Workflow disabled when selected model not loaded; Context stays off | `_syncModelControls()` / `setSuiteGating()` gating on `.loaded` | Test #19 (`gates_speed_and_workflow_on_loaded`) + UI |
| 17 | Selection preserved across refreshes (`_prevSelection`) | `loadModels()` sets/restores `_prevSelection` | Test #19 (`preserves_selection`) |
| 18 | Hardware / LM Studio URL inputs not autofill-crossed (minimal mitigation) | `autocomplete="off"` on both inputs | Test #20 + UI (#17.1 finding) |

## Existing model API (audit, per Act 18 instructions)

- **Discovery** — `src/benchmark.py::fetch_models(lm_studio_url)` calls `GET {base}/api/v1/models`,
  keeps only `type == 'llm'`, derives `loaded = len(loaded_instances) > 0` (LM Studio exposes no
  per-model top-level boolean), and preserves `max_context_length` + `loaded_instances`.
- **Normalization** — `src/benchmark.py::_normalize_model(...)` maps keys to canonical
  (`key, name, type, loaded, quantization, max_context_length, loaded_instances`) plus
  backward-compatible identity fields. No fabrication of missing metadata (None when absent).
- **HTTP layer** — `GET /api/models` returns the normalized list; `POST /api/models/load` and
  `/unload` are new in this Act (`src/routes/models.py`). Both routes consult
  `is_standard_run_active()` before mutating.

## Native LM Studio load schema (verified against installed runtime surface)

```
POST {base}/api/v1/models/load   Content-Type: application/json
Body: { "model": "<key>" }                       # required — authoritative identifier
       (+ optional advisory "n_ctx" at the standard context target, NOT an override)
Response (HTTP 200): full models payload with the requested model now carrying a loaded_instances entry
Error (404): { "error": { "type": "model_not_found", "message": "<key> not found" } }
```

The `n_ctx` value is *advisory* — it signals intent. The adapter re-reads the live models
payload and reports the **actual** instance `context_length`, never the request's `n_ctx`.

## Native LM Studio unload schema (verified against installed runtime surface)

```
POST {base}/api/v1/models/unload   Content-Type: application/json
Body: { "instance_id": "<publisher/model-id>" }  # required — the REAL instance id, NOT the model key
Response (HTTP 200): full models payload with that instance removed from loaded_instances
```

If the selected model has **zero** loaded instances → explicit `{action:"noop", status:"not_loaded"}`
(no POST issued). If it has **multiple** instances → `400 ambiguous_multiple_instances` (never a random pick).
The adapter re-reads live state before returning so downstream UI reflects reality.

## Smallest safe adapter — `src/model_lifecycle.py`

- Uses the same shared `httpx.AsyncClient(timeout=timeout)` context manager as other routes; no new deps.
- `standard_load_target(max_context) -> Optional[int]`: `min(int(max_context), 262144)` or `None`.
- `_get_models_or_conn_error(base, reason)`: wraps the GET that precedes a mutation in a transactional
  boundary and converts transport errors into `ModelLifecycleError(502, reason)`. Raw `httpx` never leaks.
- `load_model` / `unload_model`: read live state → validate (no model / unknown key / already-loaded /
  conflicting / ambiguous) → issue the minimal native call (`{model}` or `{instance_id}` only) → re-read.
- All failure paths raise `ModelLifecycleError(status_code, reason, message)`; routes map to HTTP codes
  and include `reason` in the error detail for logging/UX.

## Frontend contract — `static/index.html` + `dashboard.js` + `dashboard.css`

- New compact state line (`#model-state-line`) under the dropdown: `○ Not loaded · <quant> · Max context N`.
- New button `#load-unload-btn` (blue) to the left of the dropdown; grey Unload below, shown only when loaded.
- `fetch("/api/models/" + kind)` builds endpoints by concatenation (`kind ∈ {"load","unload"}`); both literals present.
- `setSuiteGating(!!(model && model.loaded))` gates Speed/Workflow together; Context remains disabled (Planned).

## Testing — `tests/test_model_lifecycle.py` (32 tests, all passing)

An in-memory fake emulates the native v1 surface (`GET /api/v1/models`, `POST /load {model}`,
`POST /unload {instance_id}` with mutable instance state). No GPU work, no subprocess, no DB.
Covers adapter behaviors 1–12, route HTTP mapping/guards (13–18), and frontend source-contract
gating/wiring (19–22). Full suite:

```
python -m pytest tests/test_model_lifecycle.py -q   # 32 passed
node --check static/dashboard.js                     # syntax OK
```

## Browser verification — BLOCKED (documented, not executed)

The Act requested rendered-browser verification of load→loaded / unload→not_loaded transitions.
This environment has **no Chrome/Chromium binary and no Playwright/jsdom/happy-dom installed**.
Executing a real browser test therefore requires an unbounded heavy install (~150MB Chromium),
which is not done without explicit approval. The read-only and gating behavior (render, dropdown
population, selection → Not-loaded display, Speed/Workflow disabled, selection preserved) is
proven deterministically by the unit + source-contract tests above.

**Options to unblock browser verify:**
1. Approve installing Playwright + Chromium (`npx playwright install chromium`) so a scripted
   headless render can exercise real load→loaded / unload→not_loaded transitions against LM Studio.
2. Provide an existing Chrome binary path for the harness to reuse.

## Committed files

- `src/routes/models.py` — new `GET/POST /api/models/load`, `/unload` routes (guard + mapping).
- `src/model_lifecycle.py` — adapter (`standard_load_target`, `load_model`, `unload_model`,
  `ModelLifecycleError`).
- `tests/test_model_lifecycle.py` — full suite.
- `static/index.html`, `static/dashboard.js`, `static/dashboard.css` — model-state line,
  load/unload button, gating + selection-preservation logic, dark-theme styles.
