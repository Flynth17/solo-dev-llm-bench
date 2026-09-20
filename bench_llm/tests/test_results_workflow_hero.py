"""Functional tests for the Workflow primary results experience (RM-26-AA Results UX P4).

These load the REAL static/results-shell.js in Node against a minimal DOM stub and drive
loadWorkflow() from a mock /api/ranking payload, then assert the redesigned Workflow tab:

  - model/config identity leads; fingerprints/run ids recede to quiet provenance;
  - the headline is the authoritative raw passed/total (+ "raw pass rate", never an approved
    score);
  - diagnostic/incomplete runs stay ineligible with a concise secondary note (never FAILED,
    never dominating the benchmark result); eligible runs are not mislabeled;
  - all five suites render with authoritative values, aligned by canonical suite NAME even
    when the backend returns them shuffled;
  - failure disclosure is progressive (summary -> suites with failures -> failed-check counts)
    and collapses by default;
  - every run keeps its dedicated /v2/results/{run_id} evidence link;
  - multiple runs for the same config stay individually inspectable; configs are not mixed;
  - status uses the shared P1 layer (word + tone class, never colour-only);
  - no composite/score is invented for diagnostic evidence.

No benchmark logic is recomputed here -- buildWorkflowEntries only formats what /api/ranking
already exposes (component_score.agentic.per_suite, agentic_diagnostic_runs). Mirrors
test_results_overall_hero.py.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
UTILS_JS = STATIC_DIR / "results-utils.js"
STATUS_JS = STATIC_DIR / "results-status.js"
SHELL_JS = STATIC_DIR / "results-shell.js"


# ---------------------------------------------------------------------------
# Minimal DOM stub. Elements store innerHTML as a string; querySelectorAll(".rs-nav-item")
# returns synthetic nav buttons so the shell's click/activateView wiring can run, and repeated
# getElementById calls return the SAME cached instances (with their captured handlers).
# ---------------------------------------------------------------------------
_HARNESS_JS = r"""
const fs = require("fs");

function makeEl(id) {
    var listeners = {};
    var attrs = {};
    var cls = new Set();
    var el = {
        id: id,
        textContent: "",
        hidden: false,
        focused: false,
        classList: {
            add: function (c) { cls.add(c); },
            remove: function (c) { cls.delete(c); },
            contains: function (c) { return cls.has(c); }
        },
        addEventListener: function (type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
        dispatchEvent: function () { return true; },
        getAttribute: function (name) { return attrs[name]; },
        setAttribute: function (name, val) { attrs[name] = val; },
        removeAttribute: function (name) { delete attrs[name]; },
        hasAttribute: function (name) { return attrs[name] !== undefined; },
        focus: function () { el.focused = true; },
        // A real element dispatches its registered 'click' listeners; the shell's init calls
        // hashBtn.click() to activate + lazily load the view selected by location.hash.
        click: function () { (listeners["click"] || []).forEach(function (fn) { fn(); }); }
    };
    Object.defineProperty(el, "innerHTML", {
        get: function () { return el._html || ""; },
        set: function (v) { el._html = v || ""; }
    });
    el.querySelectorAll = function () { return []; };
    return el;
}

var els = {};
function el(id) { return els[id] || (els[id] = makeEl(id)); }

// Nav items as app-shell.js injects them synchronously. None carry the disabled class/attr so
// clicking one actually activates + loads its view. The shell's loaders map is keyed by the
// prefixed id ("view-workflow"); ensureLoaded() looks it up by that key, so expose the workflow
// nav under the same key the loader expects and drive the real loadWorkflow path end-to-end.
var navIds = ["overall", "speed", "workflow", "context", "intelligence"];
var navItems = navIds.map(function (v) {
    var n = el("nav-" + v);
    n.setAttribute("data-view", v === "workflow" ? "view-workflow" : v);
    return n;
});

var document = {
    getElementById: function (id) { return el(id); },
    querySelectorAll: function (sel) { return sel === ".rs-nav-item" ? navItems : []; }
};

// Open directly at #workflow so the shell's init activates + lazily loads the Workflow view.
var location = { pathname: "/results", hash: "#workflow" };

// fetch stub: /api/ranking resolves with a well-formed ranking payload carrying two eligible
// configs (one perfect, one with failing checks) plus an incomplete diagnostic run for the
// first config. per_suite is deliberately returned SHUFFLED to prove name-based alignment.
global.fetch = function (url) {
    if (String(url) === "/api/ranking") {
        return Promise.resolve({ ok: true, status: 200, json: function () { return RANKING; } });
    }
    return Promise.resolve({ ok: true, status: 200, json: function () { return {}; } });
};

function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
function count(hay, needle) { return hay.split(needle).length - 1; }

var RANKING = {
    composite: { status: "unavailable", reason: "composite scoring contract not yet approved" },
    available_dimensions: ["agentic"],
    all_dimensions: ["agentic", "context_degradation"],
    models: [
        {
            model_version: "Ornith 1.5 35B A3B",
            model_family: "Ornith",
            architecture: "llm",
            configurations: [
                {
                    benchmark_family: "agentic",
                    configuration_fingerprint: "ornithfp0001",
                    run_ids: ["run-elig-A"],
                    component_score: { agentic: { checks_passed: 148, checks_total: 166, fraction: 0.8915, per_suite: [
                        { suite: "drift", checks_passed: 4, checks_total: 6 },
                        { suite: "evidence", checks_passed: 22, checks_total: 30 },
                        { suite: "markdown", checks_passed: 19, checks_total: 20 },
                        { suite: "java", checks_passed: 48, checks_total: 52 },
                        { suite: "python", checks_passed: 55, checks_total: 58 }
                    ]}}
                }
            ]
        },
        {
            model_version: "Zephyr 2 12B B4B",
            model_family: "Zephyr",
            architecture: "llm",
            configurations: [
                {
                    benchmark_family: "agentic",
                    configuration_fingerprint: "zephyrfp0002",
                    run_ids: ["run-elig-B"],
                    component_score: { agentic: { checks_passed: 20, checks_total: 20, fraction: 1.0, per_suite: [
                        { suite: "python", checks_passed: 20, checks_total: 20 },
                        { suite: "java", checks_passed: 20, checks_total: 20 },
                        { suite: "markdown", checks_passed: 20, checks_total: 20 },
                        { suite: "evidence", checks_passed: 20, checks_total: 20 },
                        { suite: "drift", checks_passed: 20, checks_total: 20 }
                    ]}}
                }
            ]
        }
    ],
    agentic_diagnostic_runs: [
        {
            run_id: "run-diag-A",
            model_version: "Ornith 1.5 35B A3B",
            model_family: "Ornith",
            architecture: "llm",
            configuration_fingerprint: "ornithfp0001",
            status: "incomplete",
            checks_passed: 120,
            checks_total: 166,
            per_suite: [ { suite: "python", checks_passed: 40, checks_total: 58 } ]
        }
    ]
};

// --- Load the REAL production scripts in results.html page order. ---
eval(fs.readFileSync(process.argv[2], "utf8"));   // results-utils.js (escapeHtml)
global.statusPresentation = require(process.argv[3]).statusPresentation;  // results-status.js
eval(fs.readFileSync(process.argv[4], "utf8"));   // results-shell.js (Workflow loader)

// Let the /api/ranking .then(renderWorkflow) microtask resolve.
function flush() { return new Promise(function (r) { setTimeout(r, 25); }); }

(async function () {
    await flush();
    var html = el("workflow-container").innerHTML;

    // 1. Model/config identity leads; fingerprint/run id recede to quiet provenance.
    assert(html.indexOf("rs-wf-model") !== -1, "Model/config identity must lead via .rs-wf-model");
    assert(html.indexOf("Ornith 1.5 35B A3B") !== -1, "Eligible run model identity must render as the primary element");
    assert(html.indexOf("Zephyr 2 12B B4B") !== -1, "Second config model identity must render (configs are not mixed)");
    assert(html.indexOf("ornithfp0001") !== -1, "Configuration fingerprint renders as quiet provenance");

    // 2. Headline result = authoritative raw passed/total + a clearly-labelled raw pass rate.
    assert(html.indexOf("148 / 166 checks") !== -1, "Eligible-A headline must show the authoritative 148/166 checks");
    assert(html.indexOf("89.2% raw pass rate") !== -1, "Eligible-A formats the authoritative fraction as 'raw pass rate' (verbatim)");
    assert(html.indexOf("100.0% raw pass rate") !== -1, "Perfect run (fraction 1.0) formats to 100.0% raw pass rate");

    // 3. Diagnostic/incomplete stays ineligible; eligible runs are not mislabeled as such.
    assert(count(html, "not eligible for scoring") === 1, "Exactly one diagnostic run carries the ineligibility note (run-diag-A)");
    assert(html.indexOf("inspectable diagnostic evidence") !== -1, "Diagnostic run keeps the inspectable-diagnostic-evidence marker");
    assert(html.indexOf("INCOMPLETE") !== -1, "Diagnostic run shows INCOMPLETE via the shared P1 layer");
    assert(count(html, "COMPLETED") === 2, "Only the two eligible runs are marked COMPLETED -- never the diagnostic run");

    // 4. All five suites render with authoritative values (word always visible).
    ["Python", "Java", "Markdown", "Evidence", "Drift"].forEach(function (l) {
        assert(html.indexOf("rs-wf-suite-name'>" + l + "</span>") !== -1, "Suite '" + l + "' must render as a named row");
    });
    assert(html.indexOf("55/58") !== -1 && html.indexOf("48/52") !== -1 && html.indexOf("19/20") !== -1 &&
        html.indexOf("22/30") !== -1 && html.indexOf("4/6") !== -1, "Per-suite passed/total values are authoritative");

    // 5. Suites align by canonical NAME even though the backend returned them shuffled.
    var order = ["Python", "Java", "Markdown", "Evidence", "Drift"];
    var idx = order.map(function (l) { return html.indexOf("rs-wf-suite-name'>" + l + "</span>"); });
    assert(idx.every(function (i) { return i !== -1; }), "All five suite labels present");
    assert(idx[0] < idx[1] && idx[1] < idx[2] && idx[2] < idx[3] && idx[3] < idx[4],
        "Suites render in canonical name order (python<java<markdown<evidence<drift) despite shuffled backend input");

    // 6. Failure disclosure is progressive and collapses by default; failed-check counts show.
    assert(html.indexOf("rs-wf-failures") !== -1, "Eligible run exposes progressive failure disclosure (.rs-wf-failures)");
    assert(html.indexOf("across 5 suites") !== -1, "Failure summary surfaces the per-suite breakdown for eligible-A (5 failing suites)");
    assert(html.indexOf("<li>") !== -1, "Failed-check detail list is populated with per-suite failing counts");

    // 7. Evidence link remains available per run (dedicated /v2/results/{run_id} route).
    assert(html.indexOf("/v2/results/run-elig-A") !== -1 && html.indexOf("/v2/results/run-diag-A") !== -1 &&
        html.indexOf("/v2/results/run-elig-B") !== -1, "Every run keeps its dedicated /v2/results/{run_id} evidence link");

    // 8. Multiple runs for the same config stay individually inspectable; configs not mixed.
    assert(count(html, 'class="rs-wf-run"') === 3, "Three distinct run cards render (elig-A, elig-B, diag-A) -- multiple runs individually inspectable");
    assert(html.indexOf("run-elig-A") !== -1 && html.indexOf("run-diag-A") !== -1 && html.indexOf("run-elig-B") !== -1, "Each run surfaces its own run id on its card");

    // 9. Status uses the shared P1 layer (word + tone class, never colour-only).
    assert(html.indexOf("rs-chip-positive") !== -1, "Eligible runs' COMPLETED chip comes from the shared P1 layer (positive tone)");
    assert(html.indexOf("rs-chip-warning") !== -1, "Diagnostic run's INCOMPLETE chip comes from the shared P1 layer (warning tone)");

    // 10. No composite/score is invented for diagnostic evidence; raw counts stay authoritative.
    assert(html.indexOf("rs-wf-suite-val") !== -1, "Suite values render through the presentation layer (no recomputed totals)");

    console.log("OK: results-shell Workflow hero functional checks passed");
})();
"""


def _run_harness(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available on this machine")
    harness = tmp_path / "results_workflow_hero_harness.js"
    harness.write_text(_HARNESS_JS, encoding="utf-8")
    return subprocess.run(
        [node, str(harness), str(UTILS_JS), str(STATUS_JS), str(SHELL_JS)],
        capture_output=True,
        text=True,
    )


def test_workflow_hero_model_identity_and_headline(tmp_path):
    """Workflow leads with model/config identity and an authoritative raw passed/total headline."""
    proc = _run_harness(tmp_path)
    assert proc.returncode == 0, (
        "results-shell Workflow hero (identity/headline) checks failed\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    )


def test_workflow_hero_diagnostic_stays_ineligible(tmp_path):
    """Diagnostic/incomplete runs remain ineligible (secondary note, never FAILED); eligible runs are not mislabeled."""
    proc = _run_harness(tmp_path)
    assert proc.returncode == 0, (
        "results-shell Workflow hero (diagnostic ineligibility) checks failed\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    )


def test_workflow_hero_suites_aligned_by_name_and_authoritative(tmp_path):
    """All five suites render with authoritative values, aligned by canonical suite name."""
    proc = _run_harness(tmp_path)
    assert proc.returncode == 0, (
        "results-shell Workflow hero (suite alignment/authoritative) checks failed\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    )


def test_workflow_hero_failure_disclosure_and_evidence_link(tmp_path):
    """Progressive failure disclosure exists and every run keeps its /v2/results evidence link."""
    proc = _run_harness(tmp_path)
    assert proc.returncode == 0, (
        "results-shell Workflow hero (failure disclosure/evidence link) checks failed\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    )


def test_workflow_hero_multiple_runs_and_shared_status_layer(tmp_path):
    """Multiple runs stay individually inspectable; status uses the shared P1 layer; no score invented."""
    proc = _run_harness(tmp_path)
    assert proc.returncode == 0, (
        "results-shell Workflow hero (multiple runs/shared status) checks failed\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    )
