"""Regression tests for the Results page filter callback fix.

The /results page rendered "No Results Found" even though /api/results returned all
legitimate Speed runs: applyFilters() passed matchesFilter directly as the
Array.prototype.filter callback, so its parameters were bound to (element, index,
array) instead of (run, modelFilter, hardwareFilter, envFilter), and every run was
excluded. These tests pin both the source-level fix and the functional behavior of
the actual static/results-filters.js file.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
FILTERS_JS = STATIC_DIR / "results-filters.js"


def _filters_source() -> str:
    return FILTERS_JS.read_text(encoding="utf-8")


def test_bare_matches_filter_callback_removed():
    """The buggy bare-callback pattern must not come back."""
    src = _filters_source()
    assert ".filter(matchesFilter)" not in src, (
        "results-filters.js must not pass matchesFilter as a bare filter callback: "
        "Array.prototype.filter binds its parameters to (element, index, array), "
        "which excludes every run and renders the empty state."
    )


# Loads the actual production file under test with minimal DOM stubs. Top-level
# sloppy-mode eval lets the harness reference matchesFilter/applyFilters/clearFilters
# and the filter element variables directly, mirroring the shared browser global scope.
HARNESS_JS = r"""
const fs = require("fs");

// --- Minimal DOM stubs for results-filters.js top-level wiring ---
function makeEl(value) {
    return { value: value || "", listeners: {}, addEventListener() {} };
}
var els = {};
var document = { getElementById: function (id) { if (!els[id]) els[id] = makeEl(); return els[id]; } };

// --- Globals that results.js would provide in the browser ---
var allSpeedRuns = [
    { run_id: "speed-a1", model_key: "ornith-1.5-35b-a3b",  model_display_name: "SPEED · ornith-1.5-35b-a3b",  hardware_label: "lm studio local", execution_environment: "Local" },
    { run_id: "speed-a2", model_key: "ornith-1.5-35b-a3b",  model_display_name: "SPEED · ornith-1.5-35b-a3b",  hardware_label: "lm studio local", execution_environment: "Local" },
    { run_id: "speed-b1", model_key: "ornith-2-70b",        model_display_name: "SPEED · ornith-2-70b",         hardware_label: "ollama local",      execution_environment: "Local" },
    { run_id: "speed-b2", model_key: "ornith-2-70b",        model_display_name: "SPEED · ornith-2-70b",         hardware_label: "cloud gpu",           execution_environment: "Cloud" },
    { run_id: "speed-c1", model_key: "ornith-mini-4b",      model_display_name: "SPEED · ornith-mini-4b",       hardware_label: "ollama local",      execution_environment: "Local" },
    { run_id: "speed-c2", model_key: "ornith-mini-4b",      model_display_name: "SPEED · ornith-mini-4b",       hardware_label: "cloud gpu",           execution_environment: "Cloud" },
    { run_id: "speed-n1", model_key: "nemotron-nano-9b-v2", model_display_name: "SPEED · nemotron-nano-9b-v2",  hardware_label: "ollama local",      execution_environment: "Local" }
];
var filteredSpeedRuns = [];
var renderResults = function () {};

// --- Load the actual production file under test ---
eval(fs.readFileSync(process.argv[2], "utf8"));

function assert(cond, msg) { if (!cond) { console.error("FAIL: " + msg); process.exit(1); } }

// 1. The proven regression: empty filters must keep ALL runs (previously excluded all).
applyFilters();
assert(filteredSpeedRuns.length === 7, "empty filters must keep all 7 runs, got " + filteredSpeedRuns.length);

// 2. Model filter is actually applied now.
filterModelInput.value = "nemotron";
applyFilters();
assert(filteredSpeedRuns.length === 1 && filteredSpeedRuns[0].model_key.indexOf("nemotron") !== -1,
    "model filter 'nemotron' must narrow to the nemotron run(s), got " + JSON.stringify(filteredSpeedRuns.map(function (r) { return r.model_key; })));

// 3. Hardware filter is actually applied now.
filterModelInput.value = "";
filterHardwareInput.value = "lm studio";
applyFilters();
assert(filteredSpeedRuns.length === 2 && filteredSpeedRuns.every(function (r) { return r.hardware_label.indexOf("lm studio") !== -1; }),
    "hardware filter 'lm studio' must keep exactly the 2 matching runs, got " + filteredSpeedRuns.length);

// 4. Environment filter is actually applied now.
filterHardwareInput.value = "";
filterEnvSelect.value = "Cloud";
applyFilters();
assert(filteredSpeedRuns.length === 2 && filteredSpeedRuns.every(function (r) { return r.execution_environment === "Cloud"; }),
    "env filter 'Cloud' must keep exactly the 2 Cloud runs, got " + filteredSpeedRuns.length);

// 5. clearFilters() resets control values and restores all runs.
clearFilters();
assert(filterModelInput.value === "" && filterHardwareInput.value === "" && filterEnvSelect.value === "",
    "clearFilters must reset all control values");
assert(filteredSpeedRuns.length === 7, "after clearFilters all 7 runs must be visible again, got " + filteredSpeedRuns.length);

console.log("OK: results-filters.js functional regression passed (empty-filter pass-through = 7/7)");
"""


def test_filter_pipeline_functional(tmp_path):
    """Load the real results-filters.js in Node with DOM stubs and verify behavior."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available on this machine")

    harness = tmp_path / "results_filters_harness.js"
    harness.write_text(HARNESS_JS, encoding="utf-8")
    proc = subprocess.run(
        [node, str(harness), str(FILTERS_JS)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "functional filter regression failed\nstdout: %s\nstderr: %s" % (proc.stdout, proc.stderr)
    )
