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
// Normalized Standard Speed history (point-based, from backend `speed_runs`). Rendered as
// their own run-grouped cards and NEVER blended into a legacy run-wide tok/s average.
var allSpeedRuns = [];
var filteredSpeedRuns = [];

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
        // Standard Speed runs arrive normalized (point-based) so the UI never re-implements
        // Speed semantics or invents averages -- it just renders what the read model returns.
        allSpeedRuns = Array.isArray(data.speed_runs) ? data.speed_runs : [];
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

    // Standard Speed rows still appear in `filteredRuns`, but they must render as their own
    // point-based cards -- never a legacy run-wide blended tok/s average. Exclude them by
    // run_id, using the normalized backend identity (not model name or DB row position).
    var ssRunIds = {};
    for (var i = 0; i < allSpeedRuns.length; i++) {
        var sid = allSpeedRuns[i].run_id || "";
        if (sid) ssRunIds[sid] = true;
    }
    var runs = filteredRuns.filter(function (r) { return !ssRunIds[(r.run_id || "")]; });

    // Update count -- legacy entries + Standard Speed runs, shown together.
    if (resultsCountEl) {
        if (runs.length === 0 && filteredSpeedRuns.length === 0) {
            resultsCountEl.textContent = "";
        } else {
            var counts = [];
            if (runs.length > 0) {
                // Group by run_id for counting.
                var legacyRunIds = {};
                for (var i2 = 0; i2 < runs.length; i2++) {
                    var lrid = runs[i2].run_id || "";
                    if (lrid) legacyRunIds[lrid] = true;
                }
                counts.push(runs.length + " legacy entries across " + Object.keys(legacyRunIds).length + " run" + (Object.keys(legacyRunIds).length !== 1 ? "s" : ""));
            }
            if (filteredSpeedRuns.length > 0) {
                counts.push(filteredSpeedRuns.length + " standard-speed run" + (filteredSpeedRuns.length !== 1 ? "s" : ""));
            }
            resultsCountEl.textContent = counts.join(" \u00b7 ");
        }
    }

    if (runs.length === 0 && filteredSpeedRuns.length === 0) {
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

    // Standard Speed runs as their own run-grouped, point-based cards (never blended).
    renderSpeedRuns(filteredSpeedRuns);

    // Reflect the current compare-tray selection.
    renderComparisonTray();

    // Render historical comparison chart
    renderHistoryCharts();
}

// ---------------------------------------------------------------------------
// Standard Speed history cards (point-based, grouped by run)
// Cards come from the normalized backend `speed_runs` payload -- results.js never
// re-implements Speed metrics here, and 8K/16K/32K are rendered as separate operating
// points (no run-wide blended tok/s). Canonical point labels come from target_context_tokens,
// never from actual input count.
// ---------------------------------------------------------------------------

function renderSpeedRuns(list) {
    if (!Array.isArray(list) || list.length === 0) return;
    for (var i = 0; i < list.length; i++) {
        resultsContainer.appendChild(renderStandardSpeedCard(list[i]));
    }
}

// Format a numeric value with fixed decimals, or "\u2014" when missing/NaN.
function fmtDec(value, digits) {
    var n = Number(value);
    if (value === null || value === undefined || value === "" || isNaN(n)) return "\u2014";
    return n.toFixed(digits != null ? digits : 1);
}

// Format an integer token count with thousands separators, or "\u2014" when missing.
function fmtComma(value) {
    var n = Number(value);
    if (value === null || value === undefined || value === "" || isNaN(n)) return "\u2014";
    return n.toLocaleString("en-US");
}

// Non-legacy metric versions are 2 (corrected prefill) and 3 (calibrated targets).
function isNonLegacyVersion(v) {
    var n = Number(v);
    return n === 2 || n === 3;
}

// Metric version badge. Never calls a null/legacy version "v1" -- older runs read as
// "Legacy metric" and additionally carry the prefill-warning banner below.
function metricVersionBadge(run) {
    var v = Number(run.metric_version);
    if (v === 3) return '<span class="badge badge-metric metric-v3">Metric v3</span>';
    if (v === 2) return '<span class="badge badge-metric metric-v2">Metric v2</span>';
    return '<span class="badge badge-metric metric-legacy">Legacy metric</span>';
}

function renderStandardSpeedCard(run) {
    var card = document.createElement("div");
    card.className = "history-run ss-run";

    // ---- Header: SPEED . model / run id / timestamp / version ----
    var header = document.createElement("div");
    header.className = "history-run-header ss-header";

    var title = document.createElement("span");
    title.className = "ss-title";
    title.textContent = "SPEED" + (run.model_display_name ? " \u00b7 " + run.model_display_name : "");
    header.appendChild(title);

    if (run.run_id) {
        var ridBadge = document.createElement("span");
        ridBadge.className = "badge badge-run-id";
        ridBadge.textContent = run.run_id;
        header.appendChild(ridBadge);
    }

    if (run.timestamp) {
        var ts = document.createElement("span");
        ts.className = "timestamp";
        ts.textContent = "(" + formatTimestamp(run.timestamp) + ")";
        header.appendChild(ts);
    }

    var badges = document.createElement("span");
    badges.className = "ss-badges";
    badges.innerHTML = metricVersionBadge(run);
    header.appendChild(badges);
    card.appendChild(header);

    // ---- Legacy prefill warning banner (older cached-TTFT semantics) -- never hides values.
    if (!isNonLegacyVersion(run.metric_version) || run.legacy_prefill_warning) {
        var banner = document.createElement("div");
        banner.className = "ss-legacy-banner";
        banner.textContent = "\u26A0 Legacy prefill semantics \u2014 these measurements use older cached-TTFT prefill and are not comparable to corrected-metric runs.";
        card.appendChild(banner);
    }

    // ---- Canonical points line (honest loaded-context summary; labels from target_context_tokens).
    var pointLabels = [];
    if (Array.isArray(run.points)) {
        for (var p = 0; p < run.points.length; p++) {
            if (run.points[p].label) pointLabels.push(run.points[p].label);
        }
    }
    if (pointLabels.length > 0) {
        var ptsLine = document.createElement("div");
        ptsLine.className = "ss-points-line";
        ptsLine.textContent = "Canonical points: " + pointLabels.join("\u00b7 ");
        card.appendChild(ptsLine);
    }

    // ---- Point summary grid (canonical order preserved by the read model).
    if (Array.isArray(run.points) && run.points.length > 0) {
        var grid = document.createElement("div");
        grid.className = "ss-points-grid";
        for (var i2 = 0; i2 < run.points.length; i2++) {
            grid.appendChild(renderSpeedPointCell(run.points[i2]));
        }
        card.appendChild(grid);
    }

    // ---- Dedicated result link -> /speed/results/{run_id} (NOT /v2/results).
    if (run.run_id) {
        var footer = document.createElement("div");
        footer.className = "ss-footer";
        var viewLink = document.createElement("a");
        viewLink.className = "ss-view-link";
        viewLink.href = "/speed/results/" + encodeURIComponent(run.run_id);
        viewLink.textContent = "\u2192 View Speed Result";
        footer.appendChild(viewLink);
        card.appendChild(footer);
    }

    return card;
}

// One operating point (8K / 16K / 32K). The canonical label and its target are shown
// separately from the ACTUAL input tokens, so a run-wide blended tok/s can never be invented.
function renderSpeedPointCell(point) {
    var cell = document.createElement("div");
    cell.className = "ss-point";

    var top = document.createElement("div");
    top.className = "ss-point-top";
    var label = document.createElement("span");
    label.className = "ss-point-label";
    label.textContent = point.label ? escapeHtml(String(point.label)) : "\u2014";
    top.appendChild(label);
    if (point.target_context_tokens != null) {
        var tgt = document.createElement("span");
        tgt.className = "ss-point-target";
        tgt.textContent = "target " + fmtComma(point.target_context_tokens) + " tokens";
        top.appendChild(tgt);
    }
    cell.appendChild(top);

    function metricRow(lbl, rawValueHtml) {
        var r = document.createElement("div");
        r.className = "ss-metric-row";
        r.innerHTML = '<span class="ss-metric-label">' + escapeHtml(lbl) + '</span>' +
                      '<span class="ss-metric-value">' + rawValueHtml + '</span>';
        return r;
    }

    cell.appendChild(metricRow("Input", fmtComma(point.actual_prompt_tokens) + " tokens"));
    cell.appendChild(metricRow("TTFT", formatTtft(point.ttft_seconds)));
    cell.appendChild(metricRow("Prefill", fmtDec(point.prefill_tokens_per_second, 1) + " tok/s"));
    cell.appendChild(metricRow("Generation", fmtDec(point.generation_tokens_per_second, 1) + " tok/s"));

    var extras = [];
    if (point.completion_tokens != null) {
        extras.push("Output: " + fmtComma(point.completion_tokens));
    }
    if (point.wall_time_seconds != null && String(point.wall_time_seconds).trim() !== "") {
        extras.push("Wall: " + fmtDec(point.wall_time_seconds, 1) + " s");
    }
    if (extras.length > 0) {
        cell.appendChild(metricRow("Details", escapeHtml(extras.join("\u00b7 "))));
    }

    return cell;
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

    // Inference configuration section (Act 11B)
    if (s && (typeof s.reasoning_mode !== "undefined" || typeof s.kv_cache_k_quantization !== "undefined" ||
              typeof s.flash_attention !== "undefined" || typeof s.speculative_draft_mtp !== "undefined" ||
              s.configuration_fingerprint != null || s.result_classification != null)) {
        var kvk = (typeof s.kv_cache_k_quantization !== "undefined") ? disp(s.kv_cache_k_quantization) : "\u2014";
        var kvv = (typeof s.kv_cache_v_quantization !== "undefined") ? disp(s.kv_cache_v_quantization) : "\u2014";
        var flash = (typeof s.flash_attention !== "undefined") ? (s.flash_attention ? "ON" : "OFF") : "\u2014";
        parts.push('<div class="run-metadata"><div class="metadata-title">Inference configuration</div>');
        parts.push('<div class="aggregate-item"><div class="label">Reasoning mode</div><div class="value">' + disp(s.reasoning_mode || "\u2014").toUpperCase() + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">KV cache quant (K / V)</div><div class="value">' + kvk + " / " + kvv + '</div></div>');
        parts.push('<div class="aggregate-item"><div class="label">Flash attention</div><div class="value">' + flash + '</div></div>');
        if (s.speculative_draft_mtp) {
            var mtpMin = (typeof s.speculative_draft_min_tokens !== "undefined") ? disp(s.speculative_draft_min_tokens) : "";
            var mtpMax = (typeof s.speculative_draft_max_tokens !== "undefined") ? disp(s.speculative_draft_max_tokens) : "";
            var mtpCont = (typeof s.speculative_draft_min_continue_probability !== "undefined") ? disp(s.speculative_draft_min_continue_probability) : "";
            parts.push('<div class="aggregate-item"><div class="label">MTP / speculative</div><div class="value">ON (' + mtpMin + " - " + mtpMax + " tokens" + (mtpCont ? ", keep prob " + mtpCont : "") + ') </div></div>');
        } else {
            parts.push('<div class="aggregate-item"><div class="label">MTP / speculative</div><div class="value">OFF</div></div>');
        }
        if (s.configuration_fingerprint != null) {
            var fp = "" + s.configuration_fingerprint;
            parts.push('<div class="aggregate-item"><div class="label">Config fingerprint</div><div class="value" title="' + disp(fp) + '">' + (fp.length > 16 ? fp.slice(0, 16) + "\u2026" : fp) + '</div></div>');
        }
        if (s.result_classification != null) {
            parts.push('<div class="aggregate-item"><div class="label">Result classification</div><div class="value">' + disp(s.result_classification) + '</div></div>');
        }
        parts.push('</div>');
    }

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