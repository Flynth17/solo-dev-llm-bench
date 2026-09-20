"""Functional tests for the Speed benchmark hero row + Details modal disclosure sections.

These load the REAL static/results.js in Node with minimal DOM stubs and assert the
P3 presentation behavior end-to-end:

  * Hero row -- model/config identity leads (model name primary, technical config on a
    secondary sub-line; quantization intentionally omitted, never fabricated), and the
    representative throughput cell carries an explicit "Generation throughput" headline
    label instead of a bare number.
  * Modal disclosure -- run-level target calibration, repeatability/stage evidence and
    execution provenance are demoted below the primary Speed result, each populated ONLY
    from fields precomputed by the authoritative single-run read model, and each hidden
    when its payload omits them (legacy runs render nothing fabricated).

No metric is recomputed here -- results.js only formats what /api/results and
/api/speed/runs/{id} already expose.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
RESULTS_JS = STATIC_DIR / "results.js"
SHARED_HELPER_JS = STATIC_DIR / "results-status.js"


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

// --- fetch stub: returns a per-runId payload from the MODAL_PAYLOADS map so several runs
//     can be exercised in one harness. ---
var MODAL_PAYLOADS = {};
global.fetch = function (url) {
    var id = String(url).split("/").pop();
    return Promise.resolve({ ok: true, status: 200, json: function () { return MODAL_PAYLOADS[id]; } });
};

// --- Globals results.js expects ---
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
// SCENARIO A -- Hero row structure on the benchmark table.
// Model/config identity leads; representative throughput carries a headline label.
// ===========================================================================
allSpeedRuns = [
    // Config KY (model m-y): valid points -> hero row with model name + config chips.
    { run_id: "speed-y", metric_version: 3, model_identifier: "m-y", model_display_name: "Model Y",
      hardware_label: "H100", execution_environment: "Cloud", connection_type: "cloud",
      points: [
        { label: "8K",  generation_tokens_per_second: 300 },
        { label: "16K", generation_tokens_per_second: 300 },
        { label: "32K", generation_tokens_per_second: 300 }
      ] },
    // Config KW (model m-w): all canonical points unsupported -> N/A row, 0/3 validity chip.
    { run_id: "speed-w-fail", metric_version: 3, model_identifier: "m-w", model_display_name: "Model W",
      hardware_label: "V100", execution_environment: "Cloud", connection_type: "cloud",
      points: [
        { label: "8K",  generation_tokens_per_second: null },
        { label: "16K", generation_tokens_per_second: null },
        { label: "32K", generation_tokens_per_second: null }
      ] }
];

filteredSpeedRuns = allSpeedRuns;
renderResults();
var html = document.getElementById("benchmark-body").innerHTML;

// 1. One hero block per row -- model/config identity leads the cell.
assert(count(html, "rs-speed-hero") === 2, "expected 2 hero blocks (one per row), got " + count(html, "rs-speed-hero"));

// 2. Model name is the primary identity element in each hero.
assert(count(html, "rs-speed-model") === 2, "expected 2 model-name elements (primary identity)");
assert(html.indexOf("Model Y") !== -1 && html.indexOf("Model W") !== -1, "hero must surface both model names");

// 3. Technical config drops to a secondary sub-line alongside the validity chip.
assert(count(html, "rs-speed-identity") === 2, "expected 2 identity sub-lines (secondary)");
assert(html.indexOf("0/3 points") !== -1, "fully-invalid config must show its 0/3 validity chip in the hero sub-line");

// 4. The representative throughput cell carries an explicit headline label -- not a bare number.
assert(html.indexOf("Generation throughput") !== -1, "throughput cell must carry an explicit 'Generation throughput' headline label");

// 5. Quantization is NOT surfaced by the Speed read model -- it must never be fabricated in
//    the hero identity either (only the model name + technical chips appear).
assert(html.indexOf("Quantization") === -1, "hero must not fabricate a quantization value absent from the Speed read model");

// ===========================================================================
// SCENARIO B -- Modal disclosure sections from the authoritative single-run read model.
// ===========================================================================

// Stage-aware run: one independent row per stage (1 cold + 2 warm) per canonical point, plus
// run-level calibration values precomputed by the read model.
MODAL_PAYLOADS["speed-staged"] = {
    run_id: "speed-staged",
    model_identifier: "ornith-x",
    status: "completed",
    max_abs_target_error_percent: 2.5,
    mean_target_error_tokens: -6228.0,
    configuration: {
        loaded_context: 32768,
        model_max_context: 131072,
        hardware_label: "A100",
        execution_environment: "Cloud",
        max_output_tokens: 4096,
        reasoning_mode: "disabled"
    },
    points: [
        { label: "8K", actual_prompt_tokens: 5561, ttft_seconds: 0.04, prefill_tokens_per_second: 139025, generation_tokens_per_second: 199.6, completion_tokens: 55, wall_time_seconds: 2.73,
          runs: [
            { speed_run_stage: "cold",  speed_point_status: "valid", ttft_seconds: 0.04, prefill_tokens_per_second: 139025, generation_tokens_per_second: 199.6, completion_tokens: 55, wall_time_seconds: 2.73 },
            { speed_run_stage: "warm1", speed_point_status: "valid", ttft_seconds: 0.03, prefill_tokens_per_second: 140000, generation_tokens_per_second: 201.0, completion_tokens: 55, wall_time_seconds: 2.60 },
            { speed_run_stage: "warm2", speed_point_status: "valid", ttft_seconds: 0.03, prefill_tokens_per_second: 141000, generation_tokens_per_second: 202.4, completion_tokens: 55, wall_time_seconds: 2.55 }
          ] }
    ]
};

// Legacy run: single row per point, repeatability not stored, no calibration exposed -- the
// modal must show an honest note and NO fabricated calibration/stage/provenance sections.
MODAL_PAYLOADS["speed-legacy"] = {
    run_id: "speed-legacy",
    model_identifier: "legacy-model",
    status: "completed",
    repeatability_stored: false,
    configuration: { loaded_context: 8192, hardware_label: "T4" },
    points: [ { label: "8K", actual_prompt_tokens: 5000, ttft_seconds: 0.06, prefill_tokens_per_second: 90000, generation_tokens_per_second: 120.0, completion_tokens: 40, wall_time_seconds: 3.1 } ]
};

// Run with execution provenance captured: grouped under the four contract sections, one value
// classified unknown_at_execution so it is marked distinctly (never collapsed into Not stored).
MODAL_PAYLOADS["speed-prov"] = {
    run_id: "speed-prov",
    model_identifier: "prov-model",
    status: "completed",
    configuration: { loaded_context: 16384, hardware_label: "A100" },
    points: [ { label: "8K", actual_prompt_tokens: 5000, ttft_seconds: 0.05, prefill_tokens_per_second: 120000, generation_tokens_per_second: 180.0, completion_tokens: 40, wall_time_seconds: 2.9 } ],
    provenance: {
        present: true,
        hardware: { gpu_model: { value: "NVIDIA A100", status: "stored" }, gpu_memory: { value: "80GB HBM2", status: "stored" } },
        runtime: { driver_version: { value: "535.104.05", status: "stored" }, cuda_version: { value: null, status: "unknown_at_execution" } },
        model: { architecture: { value: "transformer", status: "stored" }, quantization: { value: "fp16", status: "stored" } },
        inference: { server: { value: "vLLM 0.5", status: "stored" }, sampling_params: { value: null, status: "not_stored" } }
    }
};

async function run() {
    // B1 -- stage-aware + calibration disclosure.
    var trigger = document.getElementById("benchmark-body").querySelector(".rs-detail-btn");
    trigger.addEventListener("click", function () {});
    await new Promise(function (r) { setTimeout(r, 20); });
    openDetail("speed-staged", trigger);
    await new Promise(function (r) { setTimeout(r, 20); });
    var stagedHtml = document.getElementById("speed-detail-content").innerHTML;

    assert(stagedHtml.indexOf("Target calibration") !== -1, "modal must expose run-level target calibration");
    assert(stagedHtml.indexOf("2.5% of target") !== -1, "modal must show max absolute target error (% of target)");
    // Signed mean error is preserved (honest), never coerced to zero.
    assert(stagedHtml.indexOf("-6228.0 tokens") !== -1, "modal must preserve the signed mean error tokens, not zero it");
    assert(stagedHtml.indexOf("Repeatability") !== -1 && stagedHtml.indexOf("stage evidence") !== -1, "stage-aware modal must show repeatability/stage evidence");
    // One independent row per stage (representative cold first), verbatim from the read model.
    assert(stagedHtml.indexOf("cold") !== -1 && stagedHtml.indexOf("warm1") !== -1 && stagedHtml.indexOf("warm2") !== -1, "modal must show each stage's own run row");
    assert(stagedHtml.indexOf("199.6 tok/s") !== -1, "modal must show the representative (cold) point generation throughput");

    // B2 -- legacy run: honest note, no fabricated sections.
    await new Promise(function (r) { setTimeout(r, 5); });
    document.getElementById("speed-detail-dialog").dispatchEvent("close");
    await new Promise(function (r) { setTimeout(r, 20); });
    openDetail("speed-legacy", trigger);
    await new Promise(function (r) { setTimeout(r, 20); });
    var legacyHtml = document.getElementById("speed-detail-content").innerHTML;

    assert(legacyHtml.indexOf("Single-row evidence") !== -1, "legacy modal must show an honest single-row note");
    assert(legacyHtml.indexOf("Target calibration") === -1, "legacy run without calibration must not render a fabricated calibration section");
    assert(legacyHtml.indexOf("stage evidence") === -1, "legacy run without stages must not fabricate stage rows");

    // B3 -- provenance grouped under the four contract sections; Unknown marked distinctly.
    document.getElementById("speed-detail-dialog").dispatchEvent("close");
    await new Promise(function (r) { setTimeout(r, 5); });
    await new Promise(function (r) { setTimeout(r, 20); });
    openDetail("speed-prov", trigger);
    await new Promise(function (r) { setTimeout(r, 20); });
    var provHtml = document.getElementById("speed-detail-content").innerHTML;

    assert(provHtml.indexOf("Execution provenance") !== -1, "modal must expose execution provenance");
    ["Hardware", "Runtime", "Model", "Inference"].forEach(function (sec) {
        assert(provHtml.indexOf(sec) !== -1, "provenance must group under the " + sec + " section");
    });
    assert(provHtml.indexOf("rs-prov-unknown") !== -1, "unknown_at_execution values must be marked distinctly (never collapsed into Not stored)");

    console.log("OK: results.js hero row + modal disclosure functional checks passed");
}
run();
"""


def _run_harness(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available on this machine")
    harness = tmp_path / "results_speed_hero_harness.js"
    harness.write_text(HARNESS_JS, encoding="utf-8")
    return subprocess.run(
        [node, str(harness), str(RESULTS_JS), str(SHARED_HELPER_JS)],
        capture_output=True,
        text=True,
    )


def test_results_speed_hero_and_modal_disclosure(tmp_path):
    res = _run_harness(tmp_path)
    assert res.returncode == 0, (
        "hero/modal disclosure harness failed:\n"
        + res.stdout + "\n" + res.stderr
    )
    assert "OK: results.js hero row + modal disclosure functional checks passed" in res.stdout


def test_results_speed_hero_and_disclosure_structure():
    # Source-level structural guard: the hero markup and the three disclosure helpers must be
    # present in the real file. These are cheap (no Node) and catch a refactor that renames or
    # drops an anchor before any functional harness runs.
    src = RESULTS_JS.read_text(encoding="utf-8")

    # Hero row: identity leads, headline label on the representative throughput cell.
    assert "rs-speed-hero" in src, "renderRow must emit a hero block wrapping model/config identity"
    assert "rs-speed-model" in src, "renderRow must expose the primary model-name element"
    assert "rs-speed-identity" in src, "renderRow must expose the secondary config sub-line"
    assert "Generation throughput" in src, "representative throughput cell must carry a headline label"

    # Modal disclosure: renderModal must wire all three helpers (calibration / repeatability /
    # provenance) and none may fabricate quantization on the primary Speed surface.
    for helper in ("renderCalibration(run)", "renderRepeatability(run)", "renderProvenance(run)"):
        assert helper in src, "renderModal must call " + helper
    # The functional harness asserts the rendered row never fabricates a quantization value
    # (the read model exposes none); here we only guard the hero markup anchors above.
