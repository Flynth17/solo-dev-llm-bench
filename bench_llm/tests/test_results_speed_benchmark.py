"""Functional tests for the compact Speed benchmark table + Details modal.

These load the REAL static/results.js in Node with minimal DOM stubs and assert the
presentation behavior end-to-end: legacy exclusion, one representative row per exact
model/config (dedup by reconstructed identity), the presentation-only average generation
throughput aggregate (missing/unsupported points excluded, never zero; N/A when no valid
point), sort order, and the accessible <dialog> Details modal populated from the
authoritative single-run read model.

No benchmark logic is recomputed here -- results.js only formats what /api/results and
/api/speed/runs/{id} already expose.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
RESULTS_JS = STATIC_DIR / "results.js"
SHARED_HELPER_JS = STATIC_DIR / "results-status.js"


# ---------------------------------------------------------------------------
# Shared harness: stub globals (escapeHtml/formatTtft/fetch) + a generic element
# factory, then eval the real results.js and drive renderResults()/openDetail().
# ---------------------------------------------------------------------------
HARNESS_JS = r"""
const fs = require("fs");

// --- Globals that results-utils.js provides in the browser ---
function escapeHtml(s) {
    s = (s === null || s === undefined) ? "" : String(s);
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
}
function formatTtft(v) {
    var n = Number(v);
    if (v === null || v === undefined || v === "" || isNaN(n)) return "\u2014";
    return n.toFixed(2) + " s";
}

// --- Generic element stub supporting everything results.js touches ---
function makeEl(id) {
    var listeners = {};
    var cls = new Set();
    var el = {
        id: id,
        value: "",
        innerHTML: "",
        textContent: "",
        hidden: false,
        style: {},
        focused: false,
        classList: {
            add: function (c) { cls.add(c); },
            remove: function (c) { cls.delete(c); },
            contains: function (c) { return cls.has(c); },
            toggle: function (c) { cls.has(c) ? cls.delete(c) : cls.add(c); }
        },
        addEventListener: function (type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
        dispatchEvent: function (type) { (listeners[type] || []).forEach(function (fn) { fn(); }); return true; },
        getAttribute: function (name) { return el["_attr_" + name] || null; },
        setAttribute: function (name, val) { el["_attr_" + name] = val; },
        focus: function () { el.focused = true; },
        querySelector: function () { return makeEl(id + ":child"); },
        querySelectorAll: function () { return []; },
        closest: function () { return null; },
        showModal: function () { el._shown = true; },
        close: function () { el._closed = true; }
    };
    return el;
}
var els = {};
var document = { getElementById: function (id) { return els[id] || (els[id] = makeEl(id)); } };

// --- fetch stub: returns the modal payload for /api/speed/runs/{id} ---
var MODAL_RUN = null;
global.fetch = function (url) {
    return Promise.resolve({ ok: true, status: 200, json: function () { return MODAL_RUN; } });
};

// --- Globals results.js expects (results-filters.js would own applyFilters) ---
var allSpeedRuns = [];
var filteredSpeedRuns = [];
var renderResults = function () {};   // overwritten by the real file under eval
var applyFilters = function () {};    // no-op: results.js's loadResults() calls it after fetch

// --- Shared status helper (results-status.js) -- loaded BEFORE consumers so that
//     statusPresentation is a global exactly as in the browser build. ---
global.statusPresentation = require(process.argv[3]).statusPresentation;

// --- Load the actual production file under test ---
eval(fs.readFileSync(process.argv[2], "utf8"));

function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
function count(hay, needle) { return hay.split(needle).length - 1; }

// ===========================================================================
// SCENARIO A -- benchmark table: legacy exclusion, dedup, avg aggregate, N/A
// ===========================================================================
allSpeedRuns = [
    // Config KY (model m-y): newest valid run first -> representative; older dup deduped.
    { run_id: "speed-y-new", metric_version: 3, model_identifier: "m-y", model_display_name: "Model Y",
      hardware_label: "H100", execution_environment: "Cloud", connection_type: "cloud",
      points: [
        { label: "8K",  generation_tokens_per_second: 300 },
        { label: "16K", generation_tokens_per_second: 300 },
        { label: "32K", generation_tokens_per_second: 300 }
      ] },
    { run_id: "speed-y-old", metric_version: 3, model_identifier: "m-y", model_display_name: "Model Y",
      hardware_label: "H100", execution_environment: "Cloud", connection_type: "cloud",
      points: [ { label: "8K", generation_tokens_per_second: 999 } ] },
    // Config KX (model m-x): one unsupported point -> avg over the two valid points = 150.
    { run_id: "speed-x", metric_version: 3, model_identifier: "m-x", model_display_name: "Model X",
      hardware_label: "A100", execution_environment: "Local", connection_type: "local",
      points: [
        { label: "8K",  generation_tokens_per_second: 100 },
        { label: "16K", generation_tokens_per_second: 200 },
        { label: "32K", generation_tokens_per_second: null }
      ] },
    // Legacy (metric_version null) -> excluded from the primary surface entirely.
    { run_id: "speed-z-legacy", metric_version: null, model_identifier: "m-z", model_display_name: "Model Z",
      hardware_label: "T4", execution_environment: "Local", connection_type: "local",
      points: [ { label: "8K", generation_tokens_per_second: 42 } ] },
    // Config KW (model m-w): all canonical points unsupported -> N/A row, 0/3 valid.
    { run_id: "speed-w-fail", metric_version: 3, model_identifier: "m-w", model_display_name: "Model W",
      hardware_label: "V100", execution_environment: "Cloud", connection_type: "cloud",
      points: [
        { label: "8K",  generation_tokens_per_second: null },
        { label: "16K", generation_tokens_per_second: null },
        { label: "32K", generation_tokens_per_second: null }
      ] }
];

// Empty filters keep everything; renderResults reads the global filteredSpeedRuns.
filteredSpeedRuns = allSpeedRuns;
renderResults();

var html = document.getElementById("benchmark-body").innerHTML;
var countEl = document.getElementById("results-count");

// 1. Legacy run excluded (its model never appears).
assert(html.indexOf("Model Z") === -1, "legacy metric run must be excluded from the primary table");

// 2. One representative row per exact model/config: Model Y deduped to a single row.
assert(count(html, "Model Y") === 1, "duplicate config of Model Y must collapse to one row, got " + count(html, "Model Y"));

// 3. Three configs total (Y, X, W) -> three rows / three Details buttons.
assert(count(html, 'rs-detail-btn') === 3, "expected 3 benchmark rows, got " + count(html, 'rs-detail-btn'));

// 4. Average generation tok/s is the mean of PRESENT valid points only (100+200)/2 = 150.
assert(html.indexOf("150.0") !== -1, "Model X average must be 150.0 (mean of the two valid points), missing 32K excluded not zeroed");

// 5. Unsupported point cell renders N/A (em-dash), never 0.
assert(html.indexOf("\u2014") !== -1, "unsupported/missing generation must render as em-dash N/A");

// 6. Partial validity is visible; fully-invalid run shows 0/3 and an N/A average.
assert(html.indexOf("0/3 points") !== -1, "fully-invalid config must show a 0/3 validity chip");

// 7. Row count label reflects representative rows, not raw runs (5 runs -> 3 rows).
assert(countEl.textContent.indexOf("3 model/configs") !== -1, "count label should say 3 model/configs, got '" + countEl.textContent + "'");

// 8. Neutral default sort = alphabetical by model: W < X < Y.
var orderModel = ["Model W", "Model X", "Model Y"].map(function (m) { return html.indexOf(m); });
assert(orderModel.every(function (i) { return i !== -1; }), "all three models present in table");
assert(orderModel[0] < orderModel[1] && orderModel[1] < orderModel[2],
    "default sort must be alphabetical by model (W,X,Y); indices=" + JSON.stringify(orderModel));

// 9. Throughput sort = high -> low, nulls last: Y(300) < X(150) < W(null).
document.getElementById("speed-sort").value = "avg";
filteredSpeedRuns = allSpeedRuns;
renderResults();
var htmlAvg = document.getElementById("benchmark-body").innerHTML;
var orderAvg = ["Model Y", "Model X", "Model W"].map(function (m) { return htmlAvg.indexOf(m); });
assert(orderAvg[0] < orderAvg[1] && orderAvg[1] < orderAvg[2],
    "avg sort must order Y(300), X(150), W(null-last); indices=" + JSON.stringify(orderAvg));

// ===========================================================================
// SCENARIO B -- Details modal from the authoritative single-run read model
// ===========================================================================
MODAL_RUN = {
    run_id: "speed-modal-1",
    model_identifier: "ornith-x",
    status: "completed",
    configuration: {
        loaded_context: 32768,
        model_max_context: 131072,
        hardware_label: "A100",
        execution_environment: "Cloud",
        max_output_tokens: 4096,
        reasoning_mode: "disabled"
    },
    points: [
        { label: "8K",  actual_prompt_tokens: 5561, ttft_seconds: 0.04, prefill_tokens_per_second: 139025, generation_tokens_per_second: 199.6, completion_tokens: 55, wall_time_seconds: 2.73 },
        { label: "16K", actual_prompt_tokens: 11056, ttft_seconds: 0.04, prefill_tokens_per_second: 276400, generation_tokens_per_second: 200.16, completion_tokens: 110, wall_time_seconds: 5.1 },
        { label: "32K", actual_prompt_tokens: 21900, ttft_seconds: 0.05, prefill_tokens_per_second: 300000, generation_tokens_per_second: 188.4, completion_tokens: 210, wall_time_seconds: 11.2 }
    ]
};

var trigger = document.getElementById("benchmark-body").querySelector(".rs-detail-btn");
// Wire a real listener so the close-path focus-return can be exercised.
trigger.addEventListener("click", function () {});

(async function () {
openDetail("speed-modal-1", trigger);
// openDetail populates content via a resolved promise -- flush the microtask queue.
await new Promise(function (r) { setTimeout(r, 20); });
var modalHtml = document.getElementById("speed-detail-content").innerHTML;

assert(modalHtml.indexOf("ornith-x") !== -1, "modal must show the authoritative model identifier");
assert(modalHtml.indexOf("Run identity") !== -1, "modal must include a Run identity section");
assert(modalHtml.indexOf("32,768 tokens") !== -1, "modal must show loaded context (formatted)");
assert(modalHtml.indexOf("of 131,072") !== -1, "modal must show max context alongside loaded context");
assert(modalHtml.indexOf("A100") !== -1 && modalHtml.indexOf("Cloud") !== -1, "modal must show hardware + environment metadata");
assert(modalHtml.indexOf("4,096") !== -1, "modal must show max output tokens (formatted)");
assert(modalHtml.indexOf("199.6 tok/s") !== -1, "modal must show per-point generation throughput");
assert(modalHtml.indexOf("/speed/results/speed-modal-1") !== -1, "modal must deep-link to the dedicated single-run page");
assert(modalHtml.indexOf("Open full result") !== -1, "modal must expose an 'Open full result' control");

// Quantization is NOT surfaced by the Speed read model -- it must never be fabricated.
assert(modalHtml.indexOf("Quantization") === -1, "modal must not fabricate a quantization value absent from the Speed read model");

// Close path: native <dialog> dispatches 'close' on exit; focus returns to trigger and
// content is cleared for reuse.
document.getElementById("speed-detail-dialog").dispatchEvent("close");
assert(document.getElementById("speed-detail-content").innerHTML === "", "modal content must be cleared on close");
assert(trigger.focused === true, "focus must return to the invoking Details control on close");

console.log("OK: results.js benchmark table + Details modal functional checks passed");
})();
"""


def _run_harness(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available on this machine")
    harness = tmp_path / "results_benchmark_harness.js"
    harness.write_text(HARNESS_JS, encoding="utf-8")
    return subprocess.run(
        [node, str(harness), str(RESULTS_JS), str(SHARED_HELPER_JS)],
        capture_output=True,
        text=True,
    )


def test_results_speed_benchmark_table_and_modal(tmp_path):
    """Real results.js: legacy exclusion, dedup, avg aggregate, N/A, sort, modal."""
    proc = _run_harness(tmp_path)
    assert proc.returncode == 0, (
        "results.js functional checks failed\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    )


def test_results_css_uses_dark_shell_no_light_cards():
    """Req 2 -- the Speed page must use the persistent dark shell.

    The refactor removed every white card/table style; results.css must no longer
    contain a light (#fff) background nor any of the obsolete history-run/ss-* classes.
    """
    css = (STATIC_DIR / "results.css").read_text(encoding="utf-8")
    assert "#fff" not in css, "results.css must not use light #fff backgrounds (dark shell only)"
    assert "history-run" not in css, "obsolete white history-card styles must be removed"
    for legacy_cls in (".ss-point", ".ss-metric", ".ss-repeat", ".ss-header"):
        assert legacy_cls not in css, f"obsolete {legacy_cls} style must be removed"


def test_results_page_serves_refactored_benchmark_markup():
    """Regression -- /results still serves the compact table + accessible modal + sidebar.

    The old expandable 'Past Runs' container (results-container) is gone; the new
    benchmark tbody, native dialog and persistent-sidebar mount are present.
    """
    from fastapi.testclient import TestClient

    import src.main  # noqa: F401

    client = TestClient(src.main.app)
    body = client.get("/results").text

    assert 'id="benchmark-body"' in body, "Speed benchmark table tbody must be served"
    assert 'id="speed-detail-dialog"' in body, "accessible <dialog> Details modal must be served"
    assert "app-sidebar-mount" in body, "persistent application shell sidebar must remain"
    # The old per-run expandable container is removed by the refactor.
    assert "results-container" not in body, "obsolete expandable Past Runs container must be gone"
