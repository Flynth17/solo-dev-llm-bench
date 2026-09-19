"""Regression tests: /results#speed fresh-load initialization race (FINAL SPEED RELIABILITY GATE).

Root cause proven by clean-session trials (9/10 empty tables): a fresh navigation to
/results#speed fires GET /api/ranking and GET /api/results simultaneously. The ranking
handler is a sync ``def`` (runs in a worker thread) while the results handler is an
``async def`` (event-loop thread); both call ``ResultsStore.get_all()`` on the shared
singleton. The old ``_load_from_db()`` cleared then refilled the shared list, and
``get_all()`` returned the live reference -- so one thread's rebind could orphan the
other thread's just-filled list and hand back an empty snapshot (HTTP 200 with
``{"speed_runs": []}``), which results.js rendered as "No results".

These tests pin both halves of the fix:

Backend (src/results.py):
  - ``get_all()`` returns a snapshot copy; mutating it never affects store state.
  - Concurrent ``get_all()`` calls from multiple threads never observe an empty or
    partial list -- every caller gets the complete, identical row set.

Frontend (static/*.js, loaded in real page order against DOM stubs):
  1. Direct initial state = Speed: with location.hash == "#speed", the synchronous init
     chain activates #view-speed and marks nav-speed aria-current="page".
  2. Loader registration/activation order works: /api/ranking (Overall lazy loader) and
     /api/results (Speed data load, fired unconditionally by results.js at startup) both
     fire during the synchronous init chain, in script order, with the Speed view already
     active when the Speed data load fires. Exactly one /api/results request at init.
  3. A successful /api/results response populates the benchmark table (rows + count).
  4. Stale/older async completions cannot clear or overwrite newer rendered data: a late
    failure of an older in-flight load, and a late success carrying different data, are
    both ignored once a newer load owns state.

No browser sleeps: the Node harness resolves fetch promises explicitly, so every
assertion is deterministic.
"""

import json
import shutil
import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
UTILS_JS = STATIC_DIR / "results-utils.js"
FILTERS_JS = STATIC_DIR / "results-filters.js"
SHELL_JS = STATIC_DIR / "results-shell.js"
RESULTS_JS = STATIC_DIR / "results.js"


# ---------------------------------------------------------------------------
# Frontend harness: stub DOM mirroring results.html initial state (Overall active,
# Speed inactive; empty-state hidden), pre-created nav items simulating app-shell.js's
# documented synchronous mount+markActive (app-shell.js is fully synchronous -- no
# listeners, no async injection). Then eval the REAL production scripts in page order:
# results-utils.js -> results-filters.js -> results-shell.js -> results.js.
# ---------------------------------------------------------------------------
HARNESS_JS = r"""
const fs = require("fs");

// --- Generic element stub (mirrors what each real script touches) ---
function makeEl(id) {
    var listeners = {};
    var cls = new Set();
    var el = {
        id: id,
        value: "",
        checked: false,
        innerHTML: "",
        textContent: "",
        hidden: false,
        focused: false,
        classList: {
            add: function (c) { cls.add(c); },
            remove: function (c) { cls.delete(c); },
            contains: function (c) { return cls.has(c); },
            toggle: function (c) { if (cls.has(c)) { cls.delete(c); } else { cls.add(c); } }
        },
        addEventListener: function (type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
        dispatchEvent: function (type) { (listeners[type] || []).forEach(function (fn) { fn(); }); return true; },
        click: function () { this.dispatchEvent("click"); },
        getAttribute: function (name) { var v = el["_attr_" + name]; return v === undefined ? null : v; },
        setAttribute: function (name, val) { el["_attr_" + name] = val; },
        removeAttribute: function (name) { delete el["_attr_" + name]; },
        hasAttribute: function (name) { return el["_attr_" + name] !== undefined; },
        focus: function () { el.focused = true; },
        querySelector: function () { return el._child || (el._child = makeEl(id + ":title")); },
        querySelectorAll: function () { return []; },
        showModal: function () { el._shown = true; },
        close: function () { el._closed = true; }
    };
    return el;
}

var els = {};
function el(id) { return els[id] || (els[id] = makeEl(id)); }

// --- Nav items as app-shell.js injects them synchronously (single source of truth).
//     Default active state mirrors SIDEBAR_HTML: Overall marked active. ---
var navItems = ["overall", "speed", "workflow", "context"].map(function (v) {
    var n = el("nav-" + v);
    n.setAttribute("data-view", v);
    return n;
});
var navIntelligence = el("nav-intelligence");
navIntelligence.setAttribute("data-view", "intelligence");
navIntelligence.classList.add("rs-nav-item-disabled");
navIntelligence.setAttribute("disabled", "");
el("nav-overall").classList.add("rs-nav-item-active");
el("nav-overall").setAttribute("aria-current", "page");

// --- View containers mirroring results.html initial state: Overall active only. ---
["overall", "speed", "workflow", "context", "intelligence"].forEach(function (v) { el("view-" + v); });
el("view-overall").classList.add("rs-active");

// --- Filter controls (results-filters.js top level wires listeners on these). ---
["filter-model", "filter-hardware", "filter-env", "clear-filters"].forEach(el);

// --- Speed view elements (results.js DOM references + initial classes). ---
el("empty-state").classList.add("hidden");   // results.html: hidden until needed
["empty-state-msg", "results-count", "benchmark-panel", "benchmark-body", "speed-sort",
 "overall-container", "workflow-container", "context-container", "intelligence-container",
 "speed-detail-dialog", "speed-detail-close"].forEach(el);

var document = {
    getElementById: function (id) { return el(id); },
    querySelectorAll: function (sel) { return sel === ".rs-nav-item" ? navItems : []; }
};
var location = { pathname: "/results", hash: "#speed" };   // fresh navigation target

// --- fetch stub: /api/results gets a per-call controllable deferred; /api/ranking
//     resolves immediately with an (empty but well-formed) ranking payload. ---
var fetchLog = [];
var resultsDeferreds = [];
function activeViewNow() {
    var views = ["overall", "speed", "workflow", "context", "intelligence"];
    for (var i = 0; i < views.length; i++) {
        if (el("view-" + views[i]).classList.contains("rs-active")) { return "view-" + views[i]; }
    }
    return null;
}
global.fetch = function (url) {
    var u = String(url);
    fetchLog.push({ url: u, activeView: activeViewNow() });
    if (u === "/api/results") {
        var d = {};
        d.promise = new Promise(function (resolve, reject) { d.resolve = resolve; d.reject = reject; });
        resultsDeferreds.push(d);
        return d.promise;
    }
    // /api/ranking and anything else: immediate well-formed response.
    return Promise.resolve({ ok: true, json: function () { return { models: [], all_dimensions: [], composite: {} }; } });
};

function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }
function count(hay, needle) { return hay.split(needle).length - 1; }
var flush = function () { return new Promise(function (r) { setTimeout(r, 20); }); };

// ===========================================================================
// Load the REAL production scripts in results.html page order. app-shell.js is
// simulated by the pre-created nav items above (it is fully synchronous: mount +
// markActive from URL; no listeners). location.hash = "#speed" models a fresh
// navigation directly to /results#speed.
// ===========================================================================
eval(fs.readFileSync(process.argv[2], "utf8"));  // results-utils.js (escapeHtml/formatTtft)
eval(fs.readFileSync(process.argv[3], "utf8"));  // results-filters.js (applyFilters + listeners)
eval(fs.readFileSync(process.argv[4], "utf8"));  // results-shell.js (hash activation + Overall loader)

// ===========================================================================
// PROOF 1 -- direct initial state = Speed. The synchronous init chain must have
// activated the Speed view and nav item from the URL hash, deactivating Overall.
// ===========================================================================
assert(el("view-speed").classList.contains("rs-active"), "fresh /results#speed load must activate #view-speed");
assert(!el("view-overall").classList.contains("rs-active"), "Overall must be deactivated when #speed is active");
assert(el("nav-speed").getAttribute("aria-current") === "page", "nav-speed must carry aria-current=page after hash activation");
assert(el("nav-speed").classList.contains("rs-nav-item-active"), "nav-speed must carry the active emphasis class");
assert(!el("nav-overall").hasAttribute("aria-current"), "nav-overall must lose aria-current when Speed is activated");

// Overall lazy loader fired during the results-shell.js eval (before results.js).
var urlsAfterShell = fetchLog.map(function (e) { return e.url; });
assert(urlsAfterShell.length === 1 && urlsAfterShell[0] === "/api/ranking",
    "Overall lazy loader must fire first during init, got " + JSON.stringify(urlsAfterShell));

// Now eval results.js LAST -- its top-level loadResults() is the single init-time call.
eval(fs.readFileSync(process.argv[5], "utf8"));  // results.js (wires modal/sort + fires loadResults())

// ===========================================================================
// PROOF 2 -- loader registration/activation order. Both lazy loaders fire during the
// synchronous init chain, in script order: /api/ranking (Overall) before /api/results
// (Speed data load fired unconditionally by results.js at startup). The Speed data
// load must fire AFTER hash activation (Speed view already active), and exactly one
// /api/results request may exist at init -- no duplicate loads.
// ===========================================================================
var urls = fetchLog.map(function (e) { return e.url; });
assert(urls[0] === "/api/ranking" && urls[1] === "/api/results",
    "init chain must fire /api/ranking then /api/results in script order, got " + JSON.stringify(urls));
var resultsFetches = fetchLog.filter(function (e) { return e.url === "/api/results"; });
assert(resultsFetches.length === 1, "exactly one /api/results request at init (no duplicate loads), got " + resultsFetches.length);
assert(resultsDeferreds.length === 1, "results.js must fire exactly one /api/results fetch at startup");
assert(resultsFetches[0].activeView === "view-speed",
    "Speed data load must fire with the Speed view already active (loader order after hash activation)");

// ===========================================================================
// PROOF 3 -- a successful API response populates the table. Resolve the init-time
// request with two current-metric runs (one v2, one v3; one point unsupported).
// ===========================================================================
var RUNS = [
    { run_id: "speed-a", metric_version: 3, model_identifier: "m-a", model_display_name: "Model A",
      hardware_label: "H100", execution_environment: "Cloud", connection_type: "cloud",
      points: [
        { label: "8K", generation_tokens_per_second: 200 },
        { label: "16K", generation_tokens_per_second: 190 },
        { label: "32K", generation_tokens_per_second: 180 } ] },
    { run_id: "speed-b", metric_version: 2, model_identifier: "m-b", model_display_name: "Model B",
      hardware_label: "A100", execution_environment: "Local", connection_type: "local",
      points: [
        { label: "8K", generation_tokens_per_second: 150 },
        { label: "16K", generation_tokens_per_second: null },
        { label: "32K", generation_tokens_per_second: 140 } ] }
];

(async function () {
    resultsDeferreds[0].resolve({ ok: true, json: function () { return Promise.resolve({ speed_runs: RUNS }); } });
    await flush();

    var body = el("benchmark-body").innerHTML;
    assert(body.indexOf("Model A") !== -1 && body.indexOf("Model B") !== -1,
        "successful /api/results response must populate the benchmark table with both configs");
    assert(count(body, "rs-detail-btn") === 2, "one Details button per model/config row expected, got " + count(body, "rs-detail-btn"));
    assert(el("results-count").textContent.indexOf("2 model/configs") !== -1,
        "count label must reflect representative rows, got '" + el("results-count").textContent + "'");
    assert(el("empty-state").classList.contains("hidden"), "empty state must stay hidden when data is present");
    assert(el("benchmark-panel").hidden === false, "benchmark panel must be visible with data");

    // ===========================================================================
    // PROOF 4 -- stale/older async completions cannot clear or overwrite newer
    // rendered data. Pair A: gen 2 (older) + gen 3 (newer). The NEWER one succeeds
    // first and renders; then the OLDER one FAILS late -> its failure must not wipe
    // the newer rendered table. Pair B: gen 4 (older) + gen 5 (newer). The NEWER one
    // succeeds first; then the OLDER one SUCCEEDS late with different data -> it must
    // not overwrite the newer state.
    // ===========================================================================
    loadResults();  // gen 2 (older of pair A) -- deferred resultsDeferreds[1]
    loadResults();  // gen 3 (newer of pair A) -- deferred resultsDeferreds[2]

    var RUNS_C = [
        { run_id: "speed-c", metric_version: 3, model_identifier: "m-c", model_display_name: "Model C",
          hardware_label: "V100", execution_environment: "Cloud", connection_type: "cloud",
          points: [ { label: "8K", generation_tokens_per_second: 90 } ] }
    ];

    // Newer load (gen 3) succeeds first -> table re-renders with Model C.
    resultsDeferreds[2].resolve({ ok: true, json: function () { return Promise.resolve({ speed_runs: RUNS_C }); } });
    await flush();
    assert(el("benchmark-body").innerHTML.indexOf("Model C") !== -1, "newer successful load must render its data");

    // Older load (gen 2) FAILS late -> must not clear the newer rendered table.
    resultsDeferreds[1].reject(new Error("stale request failed"));
    await flush();
    assert(allSpeedRuns.length === RUNS_C.length && allSpeedRuns[0].run_id === "speed-c",
        "stale older failure must not clear allSpeedRuns");
    assert(el("benchmark-body").innerHTML.indexOf("Model C") !== -1, "stale older failure must not clear the rendered table");

    // Pair B: gen 4 (older) + gen 5 (newer).
    loadResults();  // gen 4 -- deferred resultsDeferreds[3]
    loadResults();  // gen 5 -- deferred resultsDeferreds[4]

    var RUNS_D = [
        { run_id: "speed-d", metric_version: 3, model_identifier: "m-d", model_display_name: "Model D",
          hardware_label: "T4", execution_environment: "Local", connection_type: "local",
          points: [ { label: "8K", generation_tokens_per_second: 60 } ] }
    ];

    // Newer load (gen 5) succeeds first -> table re-renders with Model D.
    resultsDeferreds[4].resolve({ ok: true, json: function () { return Promise.resolve({ speed_runs: RUNS_D }); } });
    await flush();
    assert(el("benchmark-body").innerHTML.indexOf("Model D") !== -1, "newer successful load (pair B) must render its data");

    // Older load (gen 4) SUCCEEDS late with different data -> must not overwrite.
    resultsDeferreds[3].resolve({ ok: true, json: function () { return Promise.resolve({ speed_runs: RUNS }); } });
    await flush();
    assert(allSpeedRuns.length === RUNS_D.length && allSpeedRuns[0].run_id === "speed-d",
        "stale older success must not overwrite newer state");
    assert(el("benchmark-body").innerHTML.indexOf("Model D") !== -1, "stale older success must not re-render older data");

    console.log("OK: /results#speed init race regression checks passed (hash activation, loader order, populate, stale-completion immunity)");
})();
"""


def _run_harness(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available on this machine")
    harness = tmp_path / "speed_init_race_harness.js"
    harness.write_text(HARNESS_JS, encoding="utf-8")
    return subprocess.run(
        [node, str(harness), str(UTILS_JS), str(FILTERS_JS), str(SHELL_JS), str(RESULTS_JS)],
        capture_output=True,
        text=True,
    )


def test_speed_init_hash_activation_loader_order_populate_stale_immunity(tmp_path):
    """Real scripts in page order: #speed hash activation, loader order, table
    population on success, and stale-completion immunity (no browser sleeps)."""
    proc = _run_harness(tmp_path)
    assert proc.returncode == 0, (
        "Speed init race regression checks failed\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    )


# ---------------------------------------------------------------------------
# Backend store race regression (src/results.py).
# ---------------------------------------------------------------------------

def _seeded_store(tmp_path, n_rows):
    from src.results import ResultsStore

    store = ResultsStore(
        csv_path=tmp_path / "results.csv",
        db_path=tmp_path / "results.db",
    )
    conn = sqlite3.connect(str(store.db_path))
    try:
        for i in range(n_rows):
            conn.execute(
                "INSERT INTO runs (run_id, model_key) VALUES (?, ?)",
                ("seed-%d" % i, "model-x"),
            )
        conn.commit()
    finally:
        conn.close()
    return store


def test_get_all_returns_snapshot_copy(tmp_path):
    """get_all() must hand back a snapshot: mutating the returned list (or the store's
    subsequent reload) never leaks into what an earlier caller is iterating."""
    store = _seeded_store(tmp_path, 5)
    snap1 = store.get_all()
    assert len(snap1) == 5

    # Mutating the snapshot must not affect store state.
    snap1.append({"run_id": "bogus"})
    assert len(store.get_all()) == 5, "mutating a get_all() snapshot must not affect the store"

    # A concurrent rebind of the shared attribute after our call must not change ours.
    store.runs = []
    assert len(snap1) == 6, "caller's snapshot must survive later shared-state swaps"


def test_concurrent_get_all_never_returns_empty_or_partial(tmp_path):
    """The exact failure mode of the fresh-load race: many threads calling get_all()
    simultaneously (sync route handlers run in worker threads while async handlers run
    on the event loop) must each receive the COMPLETE row set -- never an empty or
    partially-filled list. With atomic swap + snapshot copy this is deterministic."""
    store = _seeded_store(tmp_path, 7)
    expected_ids = {r["run_id"] for r in store.get_all()}
    assert len(expected_ids) == 7

    observations = []
    obs_lock = threading.Lock()

    def worker():
        local = []
        for _ in range(200):
            rows = store.get_all()
            local.append((len(rows), frozenset(r["run_id"] for r in rows)))
        with obs_lock:
            observations.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(observations) == 1600, "every concurrent get_all() call must complete"
    bad = [o for o in observations if o[0] != 7 or o[1] != expected_ids]
    assert not bad, (
        "%d/%d concurrent get_all() calls observed an empty/partial/divergent row set; "
        "first offender: %s" % (len(bad), len(observations), json.dumps(bad[:3], default=str))
    )
