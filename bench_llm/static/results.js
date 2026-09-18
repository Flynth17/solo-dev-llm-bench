/** Solo Dev LLM Bench - Past Results page client-side logic. */

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
var resultsPanel = document.getElementById("results-panel");
var resultsContainer = document.getElementById("results-container");
var emptyState = document.getElementById("empty-state");
var resultsCountEl = document.getElementById("results-count");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
// Normalized Standard Speed history (point-based, from backend `speed_runs`). Rendered as
// their own run-grouped cards and NEVER blended into a legacy run-wide tok/s average.
var allSpeedRuns = [];
var filteredSpeedRuns = [];

// ---------------------------------------------------------------------------
// Load results from backend
// ---------------------------------------------------------------------------
async function loadResults() {
    try {
        var resp = await fetch("/api/results");
        if (!resp.ok) return;
        var data = await resp.json();
        // Standard Speed runs arrive normalized (point-based) so the UI never re-implements
        // Speed semantics or invents averages -- it just renders what the read model returns.
        allSpeedRuns = Array.isArray(data.speed_runs) ? data.speed_runs : [];
        applyFilters();
    } catch (e) {
        allSpeedRuns = [];
        applyFilters();
    }
}

// ---------------------------------------------------------------------------
// Render Results (Raw Speed / Past Runs)
// ---------------------------------------------------------------------------
function renderResults() {
    // The Results page is a single view (Standard Speed history). No per-view gating.
    if (filteredSpeedRuns.length === 0) {
        resultsPanel.classList.add("hidden");
        emptyState.classList.remove("hidden");
        if (resultsCountEl) { resultsCountEl.textContent = ""; }
        return;
    }

    emptyState.classList.add("hidden");
    resultsPanel.classList.remove("hidden");
    if (resultsCountEl) {
        var n = filteredSpeedRuns.length;
        resultsCountEl.textContent = n + " standard-speed run" + (n !== 1 ? "s" : "");
    }

    resultsContainer.innerHTML = "";
    // Cards come from the normalized backend `speed_runs` payload -- results.js never
    // re-implements Speed metrics here.
    renderSpeedRuns(filteredSpeedRuns);
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

    // ---- Configuration / environment transparency (authoritative from the payload). --
    // Shows which configuration produced this run. Missing fields are omitted, never
    // fabricated; full quantization/architecture detail is on the dedicated page.
    var cfgChips = [];
    function chip(label, value) {
        if (value == null || String(value).trim() === "") { return; }
        cfgChips.push("<code>" + escapeHtml(String(label)) + ": " + escapeHtml(String(value)) + "</code>");
    }
    chip("Hardware", run.hardware_label);
    chip("Environment", run.execution_environment);
    chip("Connection", run.connection_type);
    if (cfgChips.length > 0) {
        var cfgLine = document.createElement("div");
        cfgLine.className = "ss-points-line";
        cfgLine.innerHTML = "Configuration: " + cfgChips.join("  ");
        card.appendChild(cfgLine);
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

    // Preserve gap / state semantics: an unsupported point is a GAP, never a zero.
    // A failed/invalid point shows its status verbatim instead of fabricated metrics.
    var st = String((point.status || "").toLowerCase());
    if (st === "unsupported" || st === "unavailable") {
        cell.appendChild(Object.assign(document.createElement("div"), {
            className: "ss-point-gap",
            textContent: "Not supported \u2014 this context point is a gap, not a zero-performance result."
        }));
        return cell;
    }
    if (st && st !== "completed") {
        cell.appendChild(Object.assign(document.createElement("div"), {
            className: "ss-point-gap ss-point-" + st,
            textContent: (point.status || "status") + " \u2014 no metrics recorded."
        }));
        return cell;
    }

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

    // Repeatability evidence (cold + warm_a + warm_b): rendered only when the backend
    // exposes per-stage runs. Absent at legacy single-row data, so this never fabricates.
    if (Array.isArray(point.runs) && point.runs.length > 1) {
        cell.appendChild(renderSpeedRunRepeatability(point.runs));
    }

    return cell;
}

// Repeatability rows: show each persisted stage's generation throughput so the cold vs
// warm distinction is inspectable. The backend owns these values; this never averages them.
function renderSpeedRunRepeatability(runs) {
    var wrap = document.createElement("div");
    wrap.className = "ss-repeat";
    wrap.appendChild(Object.assign(document.createElement("div"), {
        className: "ss-repeat-label",
        textContent: "Repeatability runs"
    }));
    runs.forEach(function (r) {
        var stage = String((r.speed_run_stage || "run").toLowerCase());
        var tps = r.generation_tokens_per_second;
        var val = (tps == null || isNaN(Number(tps))) ? "\u2014" : fmtDec(tps, 1) + " tok/s";
        var row = document.createElement("div");
        row.className = "ss-metric-row";
        row.innerHTML = '<span class="ss-metric-label">' + escapeHtml(stage) + '</span>' +
            '<span class="ss-metric-value">' + escapeHtml(val) + '</span>';
        wrap.appendChild(row);
    });
    return wrap;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadResults();