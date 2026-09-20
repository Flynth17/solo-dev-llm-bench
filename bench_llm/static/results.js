/**
 * Solo Dev LLM Bench — Standard Speed Results (compact benchmark table + Details modal).
 *
 * Renders a scannable benchmark summary table: one row per model/config with 8K/16K/32K
 * generation throughput and an average, plus an accessible native <dialog> (Details) that
 * surfaces rich per-run telemetry from the authoritative single-run read model.
 *
 * Contract (presentation only -- never redefines benchmark scoring):
 *   - Only current-metric Standard Speed runs appear (metric_version 2 or 3). Legacy runs are
 *     excluded from this surface; persisted evidence is never deleted or migrated here.
 *   - One representative run per exact model/config, grouped by reconstructed identity
 *     (model + hardware + environment + connection). The neutral rule is "latest valid
 *     current-metric run" -- the backend payload is newest-first, so the first valid run per
 *     group is the latest. No cherry-picked best/highest-throughput run is chosen.
 *   - Average generation tok/s is a presentation aggregate: arithmetic mean of present, valid
 *     canonical-point generation throughput. Missing / unsupported / failed points are excluded
 *     (never zero); no valid points -> N/A. Never manufactures 0; never a composite score.
 *   - N/A stays N/A (em-dash), never coerced to zero. No winner, no ranking label.
 */

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
var emptyState = document.getElementById("empty-state");
var emptyStateMsg = document.getElementById("empty-state-msg");
var resultsCountEl = document.getElementById("results-count");
var benchmarkPanel = document.getElementById("benchmark-panel");
var benchmarkBody = document.getElementById("benchmark-body");
var speedAvgChart = document.getElementById("speed-avg-chart");
var speedSort = document.getElementById("speed-sort");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
// Normalized Standard Speed history (point-based, from backend `speed_runs`). Rendered as a
// compact benchmark table -- NEVER blended into a legacy run-wide tok/s average.
var allSpeedRuns = [];
var filteredSpeedRuns = [];

// Canonical operating points (authoritative order).
var CANONICAL_POINTS = ["8K", "16K", "32K"];

// ---------------------------------------------------------------------------
// Load results from backend
// ---------------------------------------------------------------------------
// Monotonic load generation: each call claims a token; only the LATEST call's outcome may
// mutate state or render. A stale (older) completion -- an earlier request that fails or
// resolves late after a newer load already populated the table -- must never clear or
// overwrite newer rendered data.
var _loadGeneration = 0;

async function loadResults() {
    var gen = ++_loadGeneration;
    try {
        var resp = await fetch("/api/results");
        if (!resp.ok) return;
        var data = await resp.json();
        // Stale completion: a newer load is in flight or finished -- never apply older state.
        if (gen !== _loadGeneration) return;
        // Standard Speed runs arrive normalized (point-based) so the UI never re-implements
        // Speed semantics or invents averages -- it just renders what the read model returns.
        allSpeedRuns = Array.isArray(data.speed_runs) ? data.speed_runs : [];
        applyFilters();
    } catch (e) {
        // Stale failure: a newer load owns state now -- never clear its rendered data.
        if (gen !== _loadGeneration) return;
        allSpeedRuns = [];
        applyFilters();
    }
}

// ---------------------------------------------------------------------------
// Small formatting helpers (shared results-utils.js provides escapeHtml / formatTtft).
// ---------------------------------------------------------------------------
function fmtDec(value, digits) {
    var n = Number(value);
    if (value === null || value === undefined || value === "" || isNaN(n)) return "\u2014";
    return n.toFixed(digits != null ? digits : 1);
}

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

// ---------------------------------------------------------------------------
// Render Results (compact benchmark table)
// ---------------------------------------------------------------------------
function renderResults() {
    // One representative row per model/config, then sort by the current control.
    var rows = buildBenchmarkRuns(filteredSpeedRuns);
    var mode = speedSort ? speedSort.value : "avg";
    rows = sortRows(rows, mode);

    if (rows.length === 0) {
        // Distinguish "no runs at all" from "only legacy runs exist".
        if (filteredSpeedRuns.length === 0) {
            showEmpty(
                "No results",
                "There are no Standard Speed benchmark results to display. Run a benchmark from the main page to get started."
            );
        } else {
            // Runs exist but all are legacy (pre-metric-v2): excluded, not shown as current.
            showEmpty(
                "No current-metric Speed results available.",
                "Only legacy (pre-metric-v2) Speed runs exist. They are excluded from this surface; persisted evidence is not deleted."
            );
        }
        return;
    }

    if (benchmarkPanel) { benchmarkPanel.hidden = false; }
    if (emptyState) { emptyState.classList.add("hidden"); }
    renderTable(rows);
}

// ---------------------------------------------------------------------------
// Build one representative row per model/config.
// ---------------------------------------------------------------------------

// Reconstruct exact model/config identity from payload fields. The backend does not expose a
// configuration fingerprint on this surface, so the four material identity columns together
// define "the same config" -- changing any one yields a different group.
function runConfigKey(run) {
    return (run.model_identifier || "") + "\u0000" +
        (run.hardware_label || "") + "\u0000" +
        (run.execution_environment || "") + "\u0000" +
        (run.connection_type || "");
}

function pointByLabel(points, label) {
    if (!Array.isArray(points)) { return null; }
    for (var i = 0; i < points.length; i++) {
        if (points[i] && String(points[i].label) === label) { return points[i]; }
    }
    return null;
}

// A generation value counts as "present and valid" only when it is a positive, finite number.
// Missing / unsupported / failed points yield null -- never zero, never a fabricated metric.
function genValue(point) {
    if (!point) { return null; }
    var g = point.generation_tokens_per_second;
    if (typeof g !== "number" || isNaN(g) || g <= 0) { return null; }
    return g;
}

function runHasValidPoint(run) {
    for (var i = 0; i < CANONICAL_POINTS.length; i++) {
        if (genValue(pointByLabel(run.points, CANONICAL_POINTS[i])) !== null) { return true; }
    }
    return false;
}

function validPointCount(run) {
    var count = 0;
    for (var i = 0; i < CANONICAL_POINTS.length; i++) {
        if (genValue(pointByLabel(run.points, CANONICAL_POINTS[i])) !== null) { count++; }
    }
    return count;
}

// Presentation aggregate only: arithmetic mean of present, valid canonical-point generation
// throughput. No valid points -> null (rendered N/A). Never a benchmark score.
function avgGenTps(run) {
    var sum = 0;
    var n = 0;
    for (var i = 0; i < CANONICAL_POINTS.length; i++) {
        var g = genValue(pointByLabel(run.points, CANONICAL_POINTS[i]));
        if (g !== null) { sum += g; n++; }
    }
    return n > 0 ? sum / n : null;
}

function makeBenchmarkRow(run) {
    return {
        runId: run.run_id,
        modelLabel: run.model_display_name || run.model_identifier || "\u2014",
        hardware: run.hardware_label || "",
        env: run.execution_environment || "",
        conn: run.connection_type || "",
        metricVersion: run.metric_version,
        gen8k: genValue(pointByLabel(run.points, "8K")),
        gen16k: genValue(pointByLabel(run.points, "16K")),
        gen32k: genValue(pointByLabel(run.points, "32K")),
        avg: avgGenTps(run),
        validCount: validPointCount(run)
    };
}

function buildBenchmarkRuns(speedRuns) {
    // Req 1: exclude legacy metric runs from the primary surface.
    var current = [];
    for (var i = 0; i < speedRuns.length; i++) {
        if (isNonLegacyVersion(speedRuns[i].metric_version)) { current.push(speedRuns[i]); }
    }

    // Group by exact model/config identity. Input is newest-first, so the first valid run per
    // group is the latest valid current-metric run -- a neutral representative, not cherry-picked.
    var groups = {};
    var order = [];
    for (var j = 0; j < current.length; j++) {
        var key = runConfigKey(current[j]);
        if (!(key in groups)) { groups[key] = []; order.push(key); }
        groups[key].push(current[j]);
    }

    var rows = [];
    for (var k = 0; k < order.length; k++) {
        var candidates = groups[order[k]];
        if (!candidates.length) { continue; }
        // Prefer the latest current-metric run with at least one valid canonical point.
        // If every run in the group failed/unsupported, still surface the latest one as an
        // N/A row (validity chip shows 0/N) rather than hiding the config entirely.
        var rep = null;
        for (var l = 0; l < candidates.length; l++) {
            if (runHasValidPoint(candidates[l])) { rep = candidates[l]; break; }
        }
        if (!rep) { rep = candidates[0]; }
        rows.push(makeBenchmarkRow(rep));
    }
    return rows;
}

function sortRows(rows, mode) {
    var out = rows.slice();
    if (mode === "avg") {
        // Throughput ordering is acceptable for a Speed benchmark table. Null averages sort last.
        out.sort(function (a, b) {
            var av = a.avg == null ? -1 : a.avg;
            var bv = b.avg == null ? -1 : b.avg;
            return bv - av;
        });
    } else {
        // Neutral default: alphabetical by model -- no implied quality ranking.
        out.sort(function (a, b) {
            return String(a.modelLabel).localeCompare(String(b.modelLabel));
        });
    }
    return out;
}

// ---------------------------------------------------------------------------
// Average Generation Throughput summary (horizontal bars, fixed 0-500 tok/s scale).
// Presentation only: one bar per visible model/config row, using the exact same
// authoritative average value as the benchmark table. Rows without a valid average
// show an explicit gap -- never zero. No quality verdict language on this surface.
// ---------------------------------------------------------------------------
var AVG_CHART_MAX = 500; // fixed X-axis range (tok/s) -- stable across filters/sorts
var AVG_CHART_TICKS = [0, 100, 200, 300, 400, 500];

function renderAvgChart(rows) {
    if (!speedAvgChart) { return; }
    var withValues = [];
    for (var i = 0; i < rows.length; i++) { if (rows[i].avg != null) { withValues.push(rows[i]); } }

    // Nothing to summarise: hide the section entirely (never an empty axis of zeros).
    if (rows.length === 0 || withValues.length === 0) {
        speedAvgChart.hidden = true;
        speedAvgChart.innerHTML = "";
        return;
    }

    var html = "<h3 class='rs-avg-chart-title'>Average Generation Throughput</h3>" +
        "<p class='rs-avg-chart-sub'>tok/s &middot; fixed 0&ndash;500 scale &middot; same values as the table below</p>";

    // Axis header: tick labels aligned to the track column (25% steps of the fixed range).
    html += "<div class='rs-avg-chart-axis' aria-hidden='true'><span></span><span class='rs-avg-chart-axis-track'>" +
        AVG_CHART_TICKS.map(function (t) {
            return "<span style='left:" + (t / AVG_CHART_MAX * 100) + "%'>" + t + "</span>";
        }).join("") +
        "</span><span></span></div>";

    // One row per visible model/config: label | fixed-scale track | value.
    for (var j = 0; j < rows.length; j++) {
        var r = rows[j];
        var labelHtml = escapeHtml(r.modelLabel) +
            (r.hardware ? " <code>" + escapeHtml(r.hardware) + "</code>" : "");
        var trackInner, valueText, ariaValue;
        if (r.avg == null) {
            // Explicit gap: a missing / unsupported / invalid average is never rendered as zero.
            trackInner = "";
            valueText = "<span class='rs-na'>\u2014</span>";
            ariaValue = "no valid average";
        } else {
            // Fixed 0-500 scale: width is a direct fraction of the axis, stable across
            // filters and sorts. Values above the range clamp to full width (value still shown).
            var pct = Math.min(100, (r.avg / AVG_CHART_MAX) * 100);
            trackInner = "<span class='rs-avg-chart-fill' style='width:" + pct.toFixed(1) + "%'></span>";
            valueText = fmtDec(r.avg, 1);
            ariaValue = fmtDec(r.avg, 1) + " tokens per second";
        }
        html += "<button type='button' class='rs-avg-chart-row' data-run-id=\"" + escapeHtml(String(r.runId)) + "\"" +
            " aria-label=\"Average generation throughput for " + escapeHtml(r.modelLabel) + ": " + ariaValue + "\">" +
            "<span class='rs-avg-chart-label'>" + labelHtml + "</span>" +
            "<span class='rs-avg-chart-track' role='img' aria-hidden='true'>" + trackInner + "</span>" +
            "<span class='rs-avg-chart-value'>" + valueText + "</span>" +
            "</button>";
    }

    speedAvgChart.hidden = false;
    speedAvgChart.innerHTML = html;

    // Optional interaction: clicking a bar scrolls to and briefly highlights the matching table row.
    var chartRows = speedAvgChart.querySelectorAll(".rs-avg-chart-row");
    Array.prototype.forEach.call(chartRows, function (el) {
        el.addEventListener("click", function () {
            if (!benchmarkBody) { return; }
            var trs = benchmarkBody.querySelectorAll("tr[data-run-id]");
            for (var k = 0; k < trs.length; k++) {
                if (trs[k].getAttribute("data-run-id") === el.getAttribute("data-run-id")) {
                    var targetRow = trs[k]; // capture the element -- loop index is stale in the timer
                    targetRow.scrollIntoView({ behavior: "smooth", block: "center" });
                    targetRow.classList.add("rs-bench-row-highlight");
                    setTimeout(function () { targetRow.classList.remove("rs-bench-row-highlight"); }, 1600);
                    break;
                }
            }
        });
    });
}

// ---------------------------------------------------------------------------
// Table rendering
// ---------------------------------------------------------------------------
function renderTable(rows) {
    if (!benchmarkBody) { return; }

    // Presentation scale for the avg bar: max across visible rows only. Never a benchmark score.
    var maxAvg = 0;
    for (var i = 0; i < rows.length; i++) {
        if (rows[i].avg != null && rows[i].avg > maxAvg) { maxAvg = rows[i].avg; }
    }

    var html = "";
    for (var j = 0; j < rows.length; j++) {
        html += renderRow(rows[j], maxAvg);
    }
    benchmarkBody.innerHTML = html;

    // Average Generation Throughput summary above the table: same rows, same values,
    // fixed 0-500 tok/s scale -- rendered after the table so both stay in lockstep.
    renderAvgChart(rows);

    // Wire Details buttons.
    var btns = benchmarkBody.querySelectorAll(".rs-detail-btn");
    Array.prototype.forEach.call(btns, function (btn) {
        btn.addEventListener("click", function (e) {
            openDetail(e.currentTarget.getAttribute("data-run-id"), e.currentTarget);
        });
    });

    if (resultsCountEl) {
        var n = rows.length;
        resultsCountEl.textContent = n + " model/config" + (n !== 1 ? "s" : "");
    }
}

function renderRow(r, maxAvg) {
    var genCell = function (g) { return g === null ? "\u2014" : fmtDec(g, 1); };

    var avgHtml;
    if (r.avg == null) {
        avgHtml =
            "<div class='rs-speed-headline-label'>Generation throughput</div>" +
            '<span class="rs-na">\u2014</span>';
    } else {
        // Relative visual aid only: length based on the current visible maximum.
        var pct = maxAvg > 0 ? Math.max(8, (r.avg / maxAvg) * 100) : 8;
        avgHtml =
            "<div class='rs-speed-headline-label'>Generation throughput</div>" +
            '<div class="rs-avg">' +
            "<span class='rs-avg-val'>" + fmtDec(r.avg, 1) + " tok/s</span>" +
            "<span class='rs-avg-track' role='img' aria-label='Average generation throughput bar'>" +
            "<span class='rs-avg-fill' style='width:" + Math.round(pct) + "%'></span>" +
            "</span></div>";
    }

    var validityChip = r.validCount < 3
        ? "<span class='rs-badge rs-badge-unavailable rs-bench-valid' title='Valid canonical points'>" + r.validCount + "/3 points</span>"
        : "";

    return "<tr data-run-id=\"" + escapeHtml(String(r.runId)) + "\">" +
        "<td>" +
        "<div class='rs-speed-hero'>" +
        "<div class='rs-speed-model'>" + escapeHtml(r.modelLabel) + "</div>" +
        "<div class='rs-speed-identity'>" + configChips(r) + validityChip + "</div>" +
        "</div>" +
        "</td>" +
        '<td class="rs-num">' + genCell(r.gen8k) + "</td>" +
        '<td class="rs-num">' + genCell(r.gen16k) + "</td>" +
        '<td class="rs-num">' + genCell(r.gen32k) + "</td>" +
        "<td class=\"rs-num\">" + avgHtml + "</td>" +
        '<td class="rs-num"><button type="button" class="btn-secondary rs-detail-btn" data-run-id="' + escapeHtml(String(r.runId)) + '">Details</button></td>' +
        "</tr>";
}

// Compact configuration identity chips (authoritative from the payload). Quantization is not
// surfaced by the Speed read model, so it is intentionally omitted rather than fabricated.
function configChips(r) {
    var v = Number(r.metricVersion);
    var verBadge = "";
    if (v === 3) { verBadge = "<span class='rs-badge rs-badge-ok'>Metric v3</span>"; }
    else if (v === 2) { verBadge = "<span class='rs-badge rs-badge-accent'>Metric v2</span>"; }

    var chips = [];
    if (verBadge) { chips.push(verBadge); }
    if (r.hardware) { chips.push("<code>" + escapeHtml(r.hardware) + "</code>"); }
    if (r.env) { chips.push("<code>" + escapeHtml(r.env) + "</code>"); }
    if (r.conn) { chips.push("<code>" + escapeHtml(r.conn) + "</code>"); }

    return "<div class='rs-bench-config'>" + chips.join(" ") + "</div>";
}

function showEmpty(title, note) {
    if (benchmarkPanel) { benchmarkPanel.hidden = true; }
    if (emptyState) {
        var titleEl = emptyState.querySelector(".rs-panel-title");
        if (titleEl) { titleEl.textContent = title; }
        if (emptyStateMsg) { emptyStateMsg.textContent = note; }
        emptyState.classList.remove("hidden");
    }
}

// ---------------------------------------------------------------------------
// Details modal (native <dialog>) -- authoritative single-run read model.
// ---------------------------------------------------------------------------
var _lastTrigger = null;

function openDetail(runId, triggerEl) {
    var dialog = document.getElementById("speed-detail-dialog");
    var content = document.getElementById("speed-detail-content");
    if (!dialog || !content) { return; }
    _lastTrigger = triggerEl || null;
    content.innerHTML = "<p class='rs-modal-loading'>Loading speed result&hellip;</p>";

    fetch("/api/speed/runs/" + encodeURIComponent(runId))
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
        .then(function (run) {
            content.innerHTML = renderModal(run);
            dialog.showModal();
        })
        .catch(function () {
            content.innerHTML =
                "<div class='rs-banner rs-banner-error'>" +
                "<div class='rs-banner-text'><span class='rs-banner-title'>Unable to load Speed result</span>" +
                "The single-run read model could not be loaded. Try refreshing the page.</div></div>";
            dialog.showModal();
        });
}

function closeDetail() {
    var dialog = document.getElementById("speed-detail-dialog");
    if (dialog) { dialog.close(); }
}

// Native <dialog> dispatches 'close' on every exit path (Escape, backdrop is blocked by the
// modal, explicit Close button). Return focus to the invoking control and clear content.
function wireModal() {
    var dialog = document.getElementById("speed-detail-dialog");
    var closeBtn = document.getElementById("speed-detail-close");
    if (closeBtn) { closeBtn.addEventListener("click", closeDetail); }
    if (dialog) {
        dialog.addEventListener("close", function () {
            var content = document.getElementById("speed-detail-content");
            if (content) { content.innerHTML = ""; }
            if (_lastTrigger && typeof _lastTrigger.focus === "function") { _lastTrigger.focus(); }
            _lastTrigger = null;
        });
    }
}

// Build the modal body from the authoritative single-run read model. Mirrors the dedicated
// /speed/results/{run_id} page fields without recomputing any metric.
function renderModal(run) {
    var cfg = run.configuration || {};
    var points = Array.isArray(run.points) ? run.points : [];
    var out = "";

    // Run identity.
    out += "<section class='rs-modal-section'>";
    out += "<div class='rs-panel-title'>Run identity</div>";
    out += modalRow("Model", escapeHtml(run.model_identifier || "\u2014"));
    out += modalRow("Run ID", "<code>" + escapeHtml(String(run.run_id || "\u2014")) + "</code>");
    if (run.status) { out += modalRow("Status", stateBadge(run.status)); }
    var ctxTxt = "\u2014";
    if (cfg.loaded_context != null) {
        ctxTxt = Number(cfg.loaded_context).toLocaleString("en-US") + " tokens";
        if (cfg.model_max_context != null) {
            ctxTxt += " <span class='rs-modal-sub'>of " + Number(cfg.model_max_context).toLocaleString("en-US") + "</span>";
        }
    }
    out += modalRow("Loaded context", ctxTxt);
    out += "</section>";

    // Runtime / config metadata (authoritative; quantization intentionally omitted -- not
    // surfaced by the Speed read model, so it is never fabricated here).
    var metaKeys = ["hardware_label", "execution_environment", "connection_type", "max_output_tokens", "reasoning_mode"];
    var metaLabels = {
        "hardware_label": "Hardware",
        "execution_environment": "Environment",
        "connection_type": "Connection",
        "max_output_tokens": "Max output tokens",
        "reasoning_mode": "Reasoning mode"
    };
    var present = metaKeys.filter(function (k) { return cfg[k] != null && String(cfg[k]).trim() !== ""; });
    out += "<section class='rs-modal-section'>";
    out += "<div class='rs-panel-title'>Runtime / config metadata</div>";
    if (present.length === 0) {
        out += "<p class='rs-modal-muted'>\u2014</p>";
    } else {
        out += "<div class='rs-modal-meta'>" +
            present.map(function (k) {
                var val = cfg[k];
                if (k === "max_output_tokens") { val = Number(val).toLocaleString("en-US"); }
                return "<div><dt>" + metaLabels[k] + "</dt><dd>" + escapeHtml(String(val)) + "</dd></div>";
            }).join("") +
            "</div>";
    }
    out += "</section>";

    // Per-point telemetry (one row per canonical point).
    out += "<section class='rs-modal-section'>";
    out += "<div class='rs-panel-title'>Context scaling &middot; TTFT &middot; prefill &middot; generation</div>";
    if (points.length === 0) {
        out += "<p class='rs-modal-muted'>No point telemetry available.</p>";
    } else {
        out += "<div class='rs-table-wrap'><table class='rs-table rs-modal-tel' aria-label='Speed result by context size'>" +
            "<thead><tr><th scope='col'>Point</th>" +
            "<th scope='col' class='rs-num'>Actual input</th>" +
            "<th scope='col' class='rs-num'>TTFT</th>" +
            "<th scope='col' class='rs-num'>Prefill</th>" +
            "<th scope='col' class='rs-num'>Generation</th>" +
            "<th scope='col' class='rs-num'>Output</th>" +
            "<th scope='col' class='rs-num'>Wall</th>" +
            "</tr></thead><tbody>";
        points.forEach(function (p) {
            out += "<tr>" +
                "<td>" + escapeHtml(String(p.label || "\u2014")) + "</td>" +
                '<td class="rs-num">' + fmtComma(p.actual_prompt_tokens) + " tokens</td>" +
                '<td class="rs-num">' + formatTtft(p.ttft_seconds) + "</td>" +
                '<td class="rs-num">' + fmtDec(p.prefill_tokens_per_second, 1) + " tok/s</td>" +
                '<td class="rs-num">' + fmtDec(p.generation_tokens_per_second, 1) + " tok/s</td>" +
                '<td class="rs-num">' + fmtComma(p.completion_tokens) + "</td>" +
                '<td class="rs-num">' + fmtDec(p.wall_time_seconds, 1) + " s</td>" +
                "</tr>";
        });
        out += "</tbody></table></div>";
    }
    out += "</section>";

    // Target calibration -- demoted below the primary Speed result but kept visible as honest
    // evidence the x-axis was calibrated on. Values are precomputed by the read model; never
    // recomputed in JS, and a signed mean error is preserved (never coerced to zero).
    out += renderCalibration(run);

    // Repeatability / stage evidence: stage-aware runs show their 1-cold / 2-warm rows;
    // legacy single-row runs get an honest note. No stages are ever fabricated.
    out += renderRepeatability(run);

    // Execution provenance grouped under Hardware / Runtime / Model / Inference, behind the
    // same progressive disclosure -- never dominates the primary Speed result.
    out += renderProvenance(run);

    // Deep link to the dedicated single-run evidence page.
    if (run.run_id) {
        out += "<div class='rs-modal-footer'><a class='btn-primary' href='/speed/results/" + encodeURIComponent(run.run_id) + ">Open full result &rarr;</a></div>";
    }
    return out;
}

function modalRow(label, valueHtml) {
    return "<div class='rs-modal-row'><span class='rs-modal-k'>" + escapeHtml(label) + "</span><span class='rs-modal-v'>" + valueHtml + "</span></div>";
}

function stateBadge(status) {
    // Presentation semantics (label/tone/chip) come from the shared results-status.js
    // layer -- never recomputed here, never colour-only (word always shown).
    var p = statusPresentation(status);
    return "<span class='rs-chip " + p.className + "' title='" + escapeHtml(p.explanation || "") + "'>" +
        escapeHtml(p.label) + "</span>";
}

// ---------------------------------------------------------------------------
// Disclosure sections (calibration / repeatability / provenance).
//
// All three consume ONLY fields precomputed by the authoritative single-run read model
// (/api/speed/runs/{id}); none recompute calibration, representative selection or stage logic
// client-side. Each is defensively gated on field presence so a run payload that omits them
// (e.g. legacy runs without a provenance snapshot) renders nothing rather than fabricating.
// ---------------------------------------------------------------------------

// Run-level calibration summary: max absolute target error (% of target) and mean signed
// error tokens across points that carry input data. Section hidden when neither value is
// exposed by the read model, so legacy runs without calibration render nothing honest.
function renderCalibration(run) {
    var maxErr = run.max_abs_target_error_percent;
    var meanErr = run.mean_target_error_tokens;
    if (maxErr == null && meanErr == null) { return ""; }
    var out = "<section class='rs-modal-section'>";
    out += "<div class='rs-panel-title'>Target calibration</div>";
    out += "<div class='rs-modal-meta'>" +
        "<div><dt>Max error</dt><dd>" +
        (maxErr != null ? fmtDec(maxErr, 1) + "% of target" : "\u2014") + "</dd></div>" +
        "<div><dt>Mean error</dt><dd>" +
        // Preserve the signed value -- a negative mean is honest evidence, never zero-filled.
        (meanErr != null ? fmtDec(meanErr, 1) + " tokens" : "\u2014") + "</dd></div>" +
        "</div></section>";
    return out;
}

// Repeatability / stage evidence. A stage-aware run persists one independent row per stage
// (1 cold + 2 warm) per canonical point -- shown verbatim, representative (cold) first. A
// legacy single-row-per-point run gets an honest note; no stages are ever fabricated.
function renderRepeatability(run) {
    var points = Array.isArray(run.points) ? run.points : [];
    var stageAware = points.some(function (p) { return Array.isArray(p.runs) && p.runs.length > 1; });
    if (!stageAware) {
        if (run.repeatability_stored === false) {
            return "<section class='rs-modal-section'>" +
                "<div class='rs-panel-title'>Repeatability</div>" +
                "<p class='rs-modal-muted'>Single-row evidence &mdash; predates the 1-cold / 2-warm " +
                "persistence contract; no repeatability stages stored.</p>" +
                "</section>";
        }
        return "";
    }
    var out = "<section class='rs-modal-section'>";
    out += "<div class='rs-panel-title'>Repeatability &middot; stage evidence</div>";
    points.forEach(function (p) {
        if (!Array.isArray(p.runs) || p.runs.length < 2) { return; }
        out += "<div class='rs-stage-group'>" +
            "<div class='rs-stage-label'>" + escapeHtml(String(p.label || "")) + " point</div>";
        out += "<table class='rs-table rs-modal-tel' aria-label='Repeatability stages for " +
            escapeHtml(String(p.label)) + "'><thead><tr>" +
            "<th scope='col'>Stage</th>" +
            "<th scope='col' class='rs-num'>TTFT</th>" +
            "<th scope='col' class='rs-num'>Prefill</th>" +
            "<th scope='col' class='rs-num'>Generation</th>" +
            "<th scope='col' class='rs-num'>Output</th>" +
            "<th scope='col' class='rs-num'>Wall</th>" +
            "</tr></thead><tbody>";
        p.runs.forEach(function (s) {
            // Per-stage status comes from the authoritative read model via the shared layer.
            var statusChip = s.speed_point_status ? stateBadge(s.speed_point_status) : "";
            out += "<tr>" +
                "<td>" + escapeHtml(String(s.speed_run_stage || "\u2014")) + " " + statusChip + "</td>" +
                '<td class="rs-num">' + formatTtft(s.ttft_seconds) + "</td>" +
                '<td class="rs-num">' + fmtDec(s.prefill_tokens_per_second, 1) + " tok/s</td>" +
                '<td class="rs-num">' + fmtDec(s.generation_tokens_per_second, 1) + " tok/s</td>" +
                '<td class="rs-num">' + fmtComma(s.completion_tokens) + "</td>" +
                '<td class="rs-num">' + fmtDec(s.wall_time_seconds, 1) + " s</td>" +
                "</tr>";
        });
        out += "</tbody></table></div>";
    });
    out += "</section>";
    return out;
}

// Run-level execution provenance, grouped under the four contract sections (Hardware /
// Runtime / Model / Inference) and classified stored / unknown_at_execution / not_stored.
// Hidden when no provenance snapshot was captured (a legacy run), so nothing is fabricated.
function renderProvenance(run) {
    var prov = run.provenance;
    if (!prov || !prov.present) { return ""; }
    var out = "<section class='rs-modal-section'>";
    out += "<div class='rs-panel-title'>Execution provenance</div>";
    var sections = [
        ["hardware", "Hardware"],
        ["hardware_extra", "GPU"],
        ["runtime", "Runtime"],
        ["model", "Model"],
        ["inference", "Inference"]
    ];
    sections.forEach(function (sec) {
        var group = prov[sec[0]];
        if (!group) { return; }
        var entries = Object.keys(group).map(function (label) {
            var f = group[label];
            var value = f.value == null ? "\u2014" : escapeHtml(String(f.value));
            // unknown_at_execution is presentation metadata from the shared classifier; it is
            // marked distinctly so Unknown never collapses into Not stored (contract §5.1).
            var cls = f.status === "unknown_at_execution" ? " rs-prov-unknown" : "";
            return "<div><dt>" + escapeHtml(label) + "</dt><dd" + (cls ? " class='" + cls + "'" : "") + ">" + value + "</dd></div>";
        }).join("");
        out += "<div class='rs-modal-prov-group'>" +
            "<div class='rs-panel-sub'>" + escapeHtml(sec[1]) + "</div>" +
            "<div class='rs-modal-meta'>" + entries + "</div></div>";
    });
    out += "</section>";
    return out;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
// Re-render the table when the sort control changes.
if (speedSort) { speedSort.addEventListener("change", function () { applyFilters(); }); }

wireModal();
loadResults();
