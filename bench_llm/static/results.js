/** Solo Dev LLM Bench - Past Results page client-side logic. */

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
var resultsPanel = document.getElementById("results-panel");
var resultsContainer = document.getElementById("results-container");
var chartsPanel = document.getElementById("charts-panel");
var chartsContainer = document.getElementById("charts-container");
var emptyState = document.getElementById("empty-state");
var resultsCountEl = document.getElementById("results-count");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
var allRuns = [];
var filteredRuns = [];

// Active view — default to "raw" (Raw Speed)
var activeView = "raw"; // "raw", "markdown", "python", "java", "unsolvable"

// ---------------------------------------------------------------------------
// Load results from backend
// ---------------------------------------------------------------------------
async function loadResults() {
    try {
        var resp = await fetch("/api/results");
        if (!resp.ok) return;
        var data = await resp.json();
        allRuns = data.results || [];
        applyFilters();
    } catch (e) {
        allRuns = [];
        applyFilters();
    }
}

// ---------------------------------------------------------------------------
// Render Results (Raw Speed / Past Runs)
// ---------------------------------------------------------------------------
function renderResults() {
    // Only render Raw Speed content when Raw Speed view is active
    if (activeView !== "raw") {
        return;
    }

    var runs = filteredRuns;

    // Update count
    if (resultsCountEl) {
        if (runs.length === 0) {
            resultsCountEl.textContent = "";
        } else {
            // Group by run_id for counting
            var runIds = {};
            for (var i = 0; i < runs.length; i++) {
                var rid = runs[i].run_id || "";
                if (rid) runIds[rid] = true;
            }
            resultsCountEl.textContent = runs.length + " entries across " + Object.keys(runIds).length + " run" + (Object.keys(runIds).length !== 1 ? "s" : "");
        }
    }

    if (runs.length === 0) {
        resultsPanel.classList.add("hidden");
        chartsPanel.classList.add("hidden");
        emptyState.classList.remove("hidden");
        return;
    }

    emptyState.classList.add("hidden");
    resultsPanel.classList.remove("hidden");
    resultsContainer.innerHTML = "";

    // Group by run_id (newest first — already ordered from backend)
    var groups = {};
    var order = [];
    for (var i = 0; i < runs.length; i++) {
        var run = runs[i];
        var rid = run.run_id || "";
        if (!rid) continue;
        if (!groups[rid]) {
            groups[rid] = { runs: [], timestamp: run.timestamp || "", model: run.model_key || run.model_display_name || "", hardware_label: run.hardware_label || "", execution_environment: run.execution_environment || "", connection_type: run.connection_type || "" };
            order.push(rid);
        }
        groups[rid].runs.push(run);
    }

    // Render each run group
    for (var idx = 0; idx < order.length; idx++) {
        var rid = order[idx];
        var group = groups[rid];
        var div = document.createElement("div");
        div.className = "history-run";

        // Header with badges
        var header = document.createElement("div");
        header.className = "history-run-header";

        var badges = "";
        if (group.model) {
            badges += '<span class="badge badge-model">' + escapeHtml(group.model) + '</span>';
        }
        if (group.hardware_label) {
            badges += '<span class="badge badge-hardware">' + escapeHtml(group.hardware_label) + '</span>';
        }
        if (group.execution_environment) {
            badges += '<span class="badge badge-env">' + escapeHtml(group.execution_environment) + '</span>';
        }
        if (group.connection_type && group.connection_type !== "None") {
            badges += '<span class="badge badge-conn">' + escapeHtml(group.connection_type) + '</span>';
        }

        // Build compare button HTML (selection toggled via delegated handler in init)
        var compareBtnHtml = '<button class="compare-btn" title="Compare this run against the comparison tray" data-run-id="' + escapeHtml(rid) + '">&#x2696;&#xFE0F;</button>';

        header.innerHTML = '<span>Run #' + (idx + 1) + ' <span class="timestamp">(' + formatTimestamp(group.timestamp) + ')</span></span>' + badges + compareBtnHtml + deleteBtnHtml;
        div.appendChild(header);

        // Compute aggregates
        var tpsValues = group.runs.filter(function (r) { return r.tokens_per_second > 0; }).map(function (r) { return r.tokens_per_second; });
        var warmRuns = group.runs.filter(function (r) { return r.cold_or_warm === "warm"; });
        var warmTps = warmRuns.filter(function (r) { return r.tokens_per_second > 0; }).map(function (r) { return r.tokens_per_second; });
        var warmTtfts = warmRuns.map(function (r) { return parseFloat(r.ttft_seconds) || 0; });

        var aggHtml = '<div class="aggregate" style="margin-top:0.5rem">';
        if (tpsValues.length > 0) {
            var avg = tpsValues.reduce(function (a, b) { return a + b; }, 0) / tpsValues.length;
            aggHtml += '<div class="aggregate-item"><div class="label">Avg tok/s</div><div class="value">' + avg.toFixed(2) + '</div></div>';
            aggHtml += '<div class="aggregate-item"><div class="label">Min tok/s</div><div class="value">' + Math.min.apply(null, tpsValues).toFixed(2) + '</div></div>';
            aggHtml += '<div class="aggregate-item"><div class="label">Max tok/s</div><div class="value">' + Math.max.apply(null, tpsValues).toFixed(2) + '</div></div>';
        } else {
            aggHtml += '<span class="unavailable">No data</span>';
        }
        aggHtml += '</div>';

        var warmHtml = "";
        if (warmTps.length > 0) {
            var warmAvg = warmTps.reduce(function (a, b) { return a + b; }, 0) / warmTps.length;
            var warmAvgTtft = warmTtfts.reduce(function (a, b) { return a + b; }, 0) / warmTtfts.length;
            warmHtml = '<div class="warm-aggregate" style="margin-top:0.5rem">' +
                '<div class="aggregate-item"><div class="label">Warm Avg</div><div class="value">' + warmAvg.toFixed(2) + ' tok/s</div></div>' +
                '<div class="aggregate-item"><div class="label">Warm TTFT</div><div class="value">' + formatTtft(warmAvgTtft) + '</div></div>' +
            '</div>';
        } else {
            warmHtml = '<div class="warm-aggregate" style="margin-top:0.5rem"><span class="unavailable">Unavailable</span></div>';
        }

        div.insertAdjacentHTML("beforeend", aggHtml);
        div.insertAdjacentHTML("beforeend", warmHtml);
        div.insertAdjacentHTML("beforeend", buildRunMetadata(group));
        resultsContainer.appendChild(div);
    }

    // Reflect the current compare-tray selection.
    renderComparisonTray();

    // Render historical comparison chart
    renderHistoryCharts();
}

// Delegate delete button clicks on results container
resultsContainer.addEventListener("click", function (e) {
    var btn = e.target.closest(".delete-btn");
    if (!btn) return;
    e.stopPropagation();

    var runId = btn.getAttribute("data-run-id");
    var model = btn.getAttribute("data-model") || "";
    var timestamp = btn.getAttribute("data-timestamp") || "";

    openDeleteModal(runId, model, timestamp);
});

// ---------------------------------------------------------------------------
// Comparison tray state (persists across renders)
// ---------------------------------------------------------------------------
var selectedRuns = {}; // run_id -> { summary, label }
var comparisonTrayEl = null;

// Fixed ordered field list for the side-by-side comparison table.
var COMPARE_FIELDS = [
    ["model", "Model"],
    ["model_quantization", "Quantization"],
    ["context_size", "Context size"],
    ["prompt_tokens", "Live prompt tokens"],
    ["ttft_seconds", "TTFT"],
    ["tokens_per_second", "Decode speed (tok/s)"],
    ["benchmark_duration_seconds", "Total duration"],
    ["peak_system_ram_human_readable", "Peak system RAM"],
    ["peak_process_rss_human_readable", "Peak process RSS"],
    ["peak_vram_human_readable", "Peak VRAM"],
    ["cpu_util_avg_pct", "CPU util avg %"],
    ["cpu_util_peak_pct", "CPU util peak %"],
    ["gpu_util_avg_pct", "GPU util avg %"],
    ["gpu_util_peak_pct", "GPU util peak %"],
    ["telemetry_sample_count", "Telemetry samples"],
    ["installed_ram_human_readable", "Installed RAM"],
    ["total_vram_human_readable", "Total VRAM"],
    ["cpu_model", "CPU model"],
    ["cpu_logical_cores", "Logical cores"],
    ["cpu_physical_cores", "Physical cores"],
    ["gpu_model", "GPU model"],
    ["nvidia_driver_version", "NVIDIA driver"],
    ["os_platform", "OS platform"],
    ["os_version", "OS version"],
    ["python_version", "Python"],
];

// ---------------------------------------------------------------------------
// Per-run metadata block (context / machine snapshot / timing / telemetry)
// Uses the presentation-ready keys added by the backend enrichment. Old rows
// without Act 7/8 metadata degrade to "\u2014" (unavailable) rather than zero.
// ---------------------------------------------------------------------------
function buildRunMetadata(group) {
    var r = group.runs && group.runs[0];
    var s = (r && r.comparison_summary && typeof r.comparison_summary === "object") ? r.comparison_summary : null;

    function disp(v) {
        if (v === null || v === undefined || v === "") return "\u2014";
        return String(v);
    }

    var parts = [];
    var meta = s || r;
    if (!meta) return "";

    // Context section
    var ctxLoaded = (s && typeof s.loaded_context !== "undefined") ? s.loaded_context : meta.loaded_context;
    parts.push('<div class="run-metadata"><div class="metadata-title">Context</div>');
    parts.push('<div class="aggregate-item"><div class="label">Max / loaded context</div><div class="value">' + disp(s && s.context_size != null ? s.context_size : meta.max_output_tokens) + (s && s.loaded_context != null ? " (" + disp(s.loaded_context) + ")" : "") + '</div></div>');
    parts.push('<div class="aggregate-item"><div class="label">Live prompt tokens</div><div class="value">' + disp(s ? s.prompt_tokens : meta.input_tokens) + '</div></div>');
    var util = (s && typeof s.context_utilisation_pct !== "undefined") ? s.context_utilisation_pct : null;
    parts.push('<div class="aggregate-item"><div class="label">Context utilisation</div><div class="value">' + (util === null || util === undefined || util === "" ? "\u2014" : disp(util) + " %") + '</div></div>');
    parts.push('</div>');

    // Machine snapshot section
    if (s && s.cpu_model) {
        var cores = [];
        if (s.cpu_logical_cores != null) cores.push("L" + disp(s.cpu_logical_cores));
        if (s.cpu_physical_cores != null) cores.push("P" + disp(s.cpu_physical_cores));
        parts.push('<div class="run-metadata"><div class="metadata-title">Machine</div>');
        parts.push('<div class="aggregate-item"><div class="label">CPU</div><div class="value">' + disp(s.cpu_model) + (cores.length ? " [" + cores.join(", ") + "]" : "") + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">Installed RAM</div><div class="value">' + disp(s.installed_ram_human_readable) + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">GPU</div><div class="value">' + disp(s.gpu_model) + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">Total VRAM</div><div class="value">' + disp(s.total_vram_human_readable) + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">Driver / OS / Python</div><div class="value">' + disp(s.nvidia_driver_version || "\u2014") + " / " + disp((s.os_platform || "") + (s.os_version ? (s.os_platform ? " " : "") + s.os_version : "")) + " / " + disp(s.python_version || "\u2014") + '</div></div>');
        parts.push('</div>');
    }

    // Timing section
    if (s && (s.ttft_seconds != null || s.tokens_per_second != null || s.benchmark_duration_seconds != null)) {
        parts.push('<div class="run-metadata"><div class="metadata-title">Timing</div>');
        parts.push('<div class="aggregate-item"><div class="label">TTFT</div><div class="value">' + formatTtft(s.ttft_seconds) + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">Decode speed</div><div class="value">' + fmt2(s.tokens_per_second) + " tok/s" + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">Total duration</div><div class="value">' + fmt2(s.benchmark_duration_seconds) + " s" + '</div></div>');
        parts.push('</div>');
    }

    // Runtime telemetry section (Act 8)
    if (s && (typeof s.peak_system_ram_human_readable !== "undefined" || typeof s.peak_vram_human_readable !== "undefined" || typeof s.cpu_util_peak_pct !== "undefined")) {
        parts.push('<div class="run-metadata"><div class="metadata-title">Runtime telemetry</div>');
        parts.push('<div class="aggregate-item"><div class="label">Peak system RAM</div><div class="value">' + disp(s.peak_system_ram_human_readable) + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">Peak process RSS</div><div class="value">' + disp(s.peak_process_rss_human_readable) + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">Peak VRAM</div><div class="value">' + disp(s.peak_vram_human_readable) + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">CPU util (avg / peak)</div><div class="value">' + (typeof s.cpu_util_avg_pct === "undefined" ? "\u2014" : disp(s.cpu_util_avg_pct)) + " % / " + (typeof s.cpu_util_peak_pct === "undefined" ? "\u2014" : disp(s.cpu_util_peak_pct)) + " %" + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">GPU util (avg / peak)</div><div class="value">' + (typeof s.gpu_util_avg_pct === "undefined" ? "\u2014" : disp(s.gpu_util_avg_pct)) + " % / " + (typeof s.gpu_util_peak_pct === "undefined" ? "\u2014" : disp(s.gpu_util_peak_pct)) + " %" + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">Telemetry samples</div><div class="value">' + disp(typeof s.telemetry_sample_count === "undefined" ? "\u2014" : s.telemetry_sample_count) + '</div></div>');
        parts.push('</div>');
    }

    return '<div class="run-metadata-block">' + parts.join("") + '</div>';
}

// ---------------------------------------------------------------------------
// Comparison tray (two-run side-by-side using pre-computed Python summary)
// ---------------------------------------------------------------------------
function ensureComparisonTray() {
    if (comparisonTrayEl) return comparisonTrayEl;
    var tray = document.createElement("section");
    tray.className = "panel";
    tray.id = "comparison-tray";
    tray.innerHTML = '<h2>Run Comparison</h2><div id="comparison-table"><span class="unavailable">No runs selected. Click &#x2696;&#xFE0F; on a run to compare.</span></div>' +
        '<div class="modal-actions" style="margin-top:0.5rem"><button id="clear-compare" class="btn-secondary">Clear comparison</button></div>';
    if (resultsPanel && resultsPanel.parentNode) {
        resultsPanel.parentNode.insertBefore(tray, resultsPanel);
    }
    var clearBtn = document.getElementById("clear-compare");
    if (clearBtn) { clearBtn.addEventListener("click", function () { selectedRuns = {}; renderComparisonTray(); }); }
    comparisonTrayEl = tray;
    return tray;
}

function renderComparisonTray() {
    var tray = ensureComparisonTray();
    var keys = Object.keys(selectedRuns);
    var countEl = document.getElementById("comparison-count");
    if (countEl) { countEl.textContent = String(keys.length); }
    tray.querySelector("#comparison-table").innerHTML = "";

    if (keys.length === 0) {
        tray.querySelector("#comparison-table").innerHTML = '<span class="unavailable">No runs selected. Click &#x2696;&#xFE0F; on a run to compare.</span>';
        return;
    }

    var limit = Math.min(keys.length, 2);
    var html = '<table class="comparison-table"><thead><tr><th>Field</th>';
    for (var i = 0; i < limit; i++) {
        var sel = selectedRuns[keys[i]];
        html += '<th><div class="comp-label">' + escapeHtml(sel.label) + '</div>' +
            '<button class="remove-compare" title="Remove from comparison" data-run-id="' + escapeHtml(keys[i]) + '">×</button></th>';
    }
    html += '</tr></thead><tbody>';

    for (var f = 0; f < COMPARE_FIELDS.length; f++) {
        var key = COMPARE_FIELDS[f][0];
        var label = COMPARE_FIELDS[f][1];
        html += '<tr><td class="comp-field">' + escapeHtml(label) + '</td>';
        for (var c = 0; c < limit; c++) {
            var summ = selectedRuns[keys[c]].summary || {};
            var v = summ[key];
            if (v === null || v === undefined || v === "") { v = "\u2014"; }
            html += '<td>' + escapeHtml(String(v)) + '</td>';
        }
        html += '</tr>';
    }
    html += '</tbody></table>';
    tray.querySelector("#comparison-table").innerHTML = html;

    if (keys.length > 2) {
        tray.querySelector("#comparison-table").innerHTML += '<div class="unavailable" style="margin-top:0.5rem">Showing first 2 selected runs; deselect extras to compare.</div>';
    }
}

document.addEventListener("click", function (e) {
    var btn = e.target.closest(".compare-btn");
    if (!btn) { return; }
    e.stopPropagation();

    var rid = btn.getAttribute("data-run-id") || "";
    // Find the group this run_id belongs to in the current rendered runs.
    var summary = null, modelLabel = "";
    for (var i = 0; i < filteredRuns.length; i++) {
        if (filteredRuns[i].run_id === rid) {
            var g = filteredRuns[i];
            summary = g.comparison_summary || null;
            modelLabel = g.model_display_name || g.model_key || rid;
            break;
        }
    }

    if (selectedRuns[rid]) {
        delete selectedRuns[rid];
        btn.classList.remove("compare-btn-active");
    } else {
        selectedRuns[rid] = { summary: summary, label: modelLabel };
        btn.classList.add("compare-btn-active");
    }
    renderComparisonTray();
});
document.addEventListener("click", function (e) {
    var btn = e.target.closest(".remove-compare");
    if (!btn) { return; }
    e.stopPropagation();
    var rid = btn.getAttribute("data-run-id") || "";
    delete selectedRuns[rid];
    // Clear the active class on any compare button for this run.
    var allBtns = document.querySelectorAll(".compare-btn[data-run-id=" + JSON.stringify(rid) + "]");
    for (var i = 0; i < allBtns.length; i++) { allBtns[i].classList.remove("compare-btn-active"); }
    renderComparisonTray();
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadResults();