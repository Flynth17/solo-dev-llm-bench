"""Functional tests for the Overall hero / benchmark coverage (RM-26-AA Results UX P2).

These load the REAL static/results-shell.js in Node against a minimal DOM stub and drive
renderOverall() from a mock /api/ranking payload, then assert the redesigned Overall tab:

  - NO COMPOSITE remains visible but as a secondary chip, never a heading;
  - no Overall/composite score is rendered (no metric-value/score token, no leaderboard);
  - no model cards / configuration rows / evidence counts render (renderL0Models suppressed);
  - delivered dimensions render as "DELIVERED" with an Open control;
  - future/research dimensions stay distinct ("RESEARCH" + backend reason, no Open control);
  - a delivered dimension's Open control activates its view (navigation works);
  - every status chip carries its uppercase word (never colour-only).

No benchmark logic is recomputed here -- renderOverall only formats what /api/ranking already
exposes (available_dimensions / dimension_reasons). Mirrors test_results_speed_benchmark.py.
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
# Minimal DOM stub. Elements store innerHTML as a string; querySelectorAll(".rs-coverage-
# open") returns synthetic button nodes parsed from that string so renderOverall's click
# wiring can be exercised, and repeated queries return the SAME cached instances (with their
# captured handlers) so the test can invoke them.
# ---------------------------------------------------------------------------
_HARNESS_JS = r"""
const fs = require("fs");

function makeEl(id) {
    var listeners = {};
    var el = {
        id: id,
        _innerHTML: "",
        focused: false,
        classList: {
            _set: new Set(),
            add: function (c) { this._set.add(c); },
            remove: function (c) { this._set.delete(c); },
            contains: function (c) { return this._set.has(c); }
        },
        addEventListener: function (type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
        dispatchEvent: function () { return true; },
        getAttribute: function (name) { return el["_attr_" + name]; },
        setAttribute: function (name, val) { el["_attr_" + name] = val; },
        removeAttribute: function (name) { delete el["_attr_" + name]; },
        hasAttribute: function (name) { return el["_attr_" + name] !== undefined; },
        focus: function () { el.focused = true; }
    };
    Object.defineProperty(el, "innerHTML", {
        get: function () { return el._innerHTML; },
        set: function (v) {
            el._innerHTML = v || "";
            // Re-parse delivered-dimension Open buttons from the rendered markup.
            el._coverageButtons = _openButtons(el._innerHTML);
        }
    });
    el.querySelectorAll = function (sel) {
        if (sel === ".rs-coverage-open") { return el._coverageButtons || []; }
        return [];
    };
    return el;
}

// Parse <button ... class='rs-coverage-open' data-dim='...'> controls from markup. Tolerant
// of single or double attribute quotes (renderOverall emits single-quoted attributes). Each
// returned button is a self-contained stub with working addEventListener/dispatchEvent so the
// click wiring renderOverall installs can actually be invoked by the test.
function _openButtons(html) {
    var re = /<button\b[^>]*class=['"]rs-coverage-open['"][^>]*data-dim=['"]([^'"]*)['"]/g;
    var m, out = [];
    while ((m = re.exec(html)) !== null) {
        // NOTE: listeners must be per-instance. A `var handlers` here would be
        // function-scoped to _openButtons, so every button would share one array and
        // dispatching a single button would fire every dimension's handler.
        var btn = {
            id: "open-" + m[1],
            _dim: m[1],
            _listeners: {},
            classList: { add: function () {}, remove: function () {}, contains: function () { return false; } },
            getAttribute: function (k) { return k === "data-dim" ? this._dim : null; },
            setAttribute: function () {},
            removeAttribute: function () {},
            hasAttribute: function () { return false; },
            addEventListener: function (type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
            dispatchEvent: function (type) { (this._listeners[type] || []).forEach(function (fn) { fn(); }); return true; }
        };
        out.push(btn);
    }
    return out;
}

var els = {};
function el(id) { return els[id] || (els[id] = makeEl(id)); }

// Nav items as app-shell.js injects them synchronously. Overall active by default.
var navIds = ["overall", "speed", "workflow", "context", "intelligence"];
var navItems = navIds.map(function (v) {
    var n = el("nav-" + v);
    n.setAttribute("data-view", v);
    return n;
});

// View containers.
navIds.forEach(function (v) { el("view-" + v); });
el("view-overall").classList.add("rs-active");

var document = {
    getElementById: function (id) { return el(id); },
    querySelectorAll: function (sel) { return sel === ".rs-nav-item" ? navItems : []; }
};

// location mirrors /results#overall (the hero loads first).
var location = { pathname: "/results", hash: "#overall" };

// fetch stub: /api/ranking resolves immediately with a well-formed ranking payload where
// Speed + agentic are delivered and Context + Intelligence are future/research.
global.fetch = function (url) {
    if (String(url) === "/api/ranking") {
        return Promise.resolve({
            ok: true,
            status: 200,
            json: function () {
                return {
                    composite: {
                        status: "unavailable",
                        reason: "Overall Solo Bench composite scoring contract is not yet approved; no Overall Solo Bench score is computed or inferred.",
                        available_dimensions: ["speed", "agentic"],
                        dimension_reasons: {
                            "context_degradation": "context_degradation results are available on the Context page",
                            "intelligence": "intelligence benchmark is not implemented"
                        }
                    },
                    available_dimensions: ["speed", "agentic"],
                    all_dimensions: ["speed", "agentic", "context_degradation", "intelligence"],
                    models: []
                };
            }
        });
    }
    return Promise.resolve({ ok: true, status: 200, json: function () { return {}; } });
};

function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }


// --- Load the REAL production scripts in results.html page order. ---
eval(fs.readFileSync(process.argv[2], "utf8"));   // results-utils.js (escapeHtml)
global.statusPresentation = require(process.argv[3]).statusPresentation;  // results-status.js
eval(fs.readFileSync(process.argv[4], "utf8"));   // results-shell.js (Overall loader)

// Let the /api/ranking .then(renderOverall) microtask resolve.
function flush() { return new Promise(function (r) { setTimeout(r, 25); }); }

(async function () {
    await flush();

    var html = el("overall-container").innerHTML;
    var openButtons = el("overall-container").querySelectorAll(".rs-coverage-open");

    // 1. NO COMPOSITE remains visible -- but as a secondary chip from the shared P1 layer,
    //    never the page heading. The strong product title leads instead.
    assert(html.indexOf("NO COMPOSITE") !== -1, "Overall must still surface the NO COMPOSITE indicator");
    assert(html.indexOf("rs-chip-neutral") !== -1, "NO COMPOSITE must be styled via the shared P1 chip layer (neutral tone)");
    assert(html.indexOf("no-composite") !== -1, "NO COMPOSITE must carry its data-status attribute");
    assert(html.indexOf("per-dimension readiness") !== -1, "Overall hero must lead with a per-dimension-readiness title");
    assert(html.indexOf('rs-panel-title">Overall') === -1,
        "The old 'Overall' panel-heading + NO COMPOSITE-as-heading pattern must be gone");

    // 2. No Overall/composite score is rendered anywhere: no metric-value/score token and
    //    no leaderboard markup (renderL0Models must be suppressed on this view).
    assert(html.indexOf("rs-metric-value") === -1, "Overall hero must not render any score/metric value");
    assert(html.indexOf("rs-model-group") === -1, "Overall must not render model groups (no partial leaderboard)");
    assert(html.indexOf("rs-config-row") === -1, "Overall must not render configuration rows (no partial leaderboard)");
    assert(html.indexOf("overall_solo_bench_score") === -1, "Overall must never expose a composite score field");

    // 3. Delivered dimensions render as DELIVERED with an Open navigation control.
    assert(html.indexOf("rs-coverage-delivered") !== -1, "Delivered dimensions must render as delivered cards");
    assert(html.indexOf(">DELIVERED<") !== -1, "Delivered dimensions must show a DELIVERED chip (word, not colour alone)");
    var deliveredDims = openButtons.filter(function (b) { return b.getAttribute("data-dim"); }).length;
    assert(deliveredDims === 2, "Exactly the two delivered dimensions must expose an Open control, got " + deliveredDims);
    assert(html.indexOf("Open Speed") !== -1 && html.indexOf("Open Workflow") !== -1,
        "Each delivered dimension must show an obvious 'Open' navigation path");

    // 4. Future/research dimensions stay distinct: RESEARCH chip + backend reason and NO
    //    Open control (they do not navigate to a live view).
    assert(html.indexOf("rs-coverage-research") !== -1, "Future/research dimensions must render as research cards");
    assert(html.indexOf(">RESEARCH<") !== -1, "Future/research dimensions must show a RESEARCH chip (word, not colour alone)");
    assert(html.indexOf("intelligence benchmark is not implemented") !== -1,
        "Research dimension must show its backend-provided reason (single source of truth)");
    var researchLeak = openButtons.some(function (b) {
        var d = b.getAttribute("data-dim");
        return d === "context_degradation" || d === "intelligence";
    });
    assert(!researchLeak, "Research/future dimensions must NOT expose a navigation control");

    // 5. Delivered dimension navigation works: clicking Speed's Open activates its view and
    //    marks the nav item active (mirrors the shell tab behaviour).
    var speedBtn = openButtons.filter(function (b) { return b.getAttribute("data-dim") === "speed"; })[0];
    assert(!!speedBtn, "Speed Open control must be a live, clickable node");
    speedBtn.dispatchEvent("click");
    assert(el("view-speed").classList.contains("rs-active"), "Clicking Speed's Open must activate the Speed view");
    assert(el("nav-speed").getAttribute("aria-current") === "page", "Clicking Speed's Open must mark nav-speed active");

    console.log("OK: results-shell Overall hero functional checks passed");
})();
"""


def _run_harness(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available on this machine")
    harness = tmp_path / "results_overall_hero_harness.js"
    harness.write_text(_HARNESS_JS, encoding="utf-8")
    return subprocess.run(
        [node, str(harness), str(UTILS_JS), str(STATUS_JS), str(SHELL_JS)],
        capture_output=True,
        text=True,
    )


def test_overall_hero_leads_with_readiness_not_composite(tmp_path):
    """Overall leads with per-dimension readiness; NO COMPOSITE is secondary, never a heading."""
    proc = _run_harness(tmp_path)
    assert proc.returncode == 0, (
        "results-shell Overall hero checks failed\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    )


def test_overall_hero_renders_no_score_or_leaderboard(tmp_path):
    """No composite score and no model/config leaderboard render on Overall."""
    proc = _run_harness(tmp_path)
    assert proc.returncode == 0, (
        "results-shell Overall hero (no-score/no-leaderboard) checks failed\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    )


def test_overall_hero_coverage_delivered_vs_research(tmp_path):
    """Delivered dimensions are actionable; future/research stay distinct with their reason."""
    proc = _run_harness(tmp_path)
    assert proc.returncode == 0, (
        "results-shell Overall hero (coverage) checks failed\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    )


def test_overall_hero_delivered_navigation_activates_view(tmp_path):
    """A delivered dimension's Open control activates its view and marks the nav active."""
    proc = _run_harness(tmp_path)
    assert proc.returncode == 0, (
        "results-shell Overall hero (navigation) checks failed\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    )
