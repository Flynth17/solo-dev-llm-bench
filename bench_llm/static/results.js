/** Solo Dev LLM Bench - Past Results page client-side logic. */

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
var filterModelInput = document.getElementById("filter-model");
var filterHardwareInput = document.getElementById("filter-hardware");
var filterEnvSelect = document.getElementById("filter-env");
var clearFiltersBtn = document.getElementById("clear-filters");
var resultsPanel = document.getElementById("results-panel");
var resultsContainer = document.getElementById("results-container");
var chartsPanel = document.getElementById("charts-panel");
var chartsContainer = document.getElementById("charts-container");
var emptyState = document.getElementById("empty-state");
var resultsCountEl = document.getElementById("results-count");

// Delete modal references
var deleteModal = document.getElementById("delete-modal");
var deleteModalBody = document.getElementById("delete-modal-body");
var cancelDeleteBtn = document.getElementById("cancel-delete");
var confirmDeleteBtn = document.getElementById("confirm-delete");

// Current pending delete state
var pendingDeleteRunId = null;
var pendingDeleteModel = "";
var pendingDeleteTimestamp = "";

// ---------------------------------------------------------------------------
// Helpers (reuse formatting from dashboard.js)
// ---------------------------------------------------------------------------

function fmt2(value) {
    if (value === null || value === undefined || value === "") return "\u2014";
    var num = parseFloat(value);
    if (isNaN(num)) return "\u2014";
    return num.toFixed(2);
}

function fmtInt(value) {
    if (value === null || value === undefined || value === "") return "\u2014";
    var num = parseInt(value, 10);
    if (isNaN(num)) return "\u2014";
    return num.toString();
}

function formatTtft(value) {
    if (value === null || value === undefined || value === "") return "\u2014";
    var num = parseFloat(value);
    if (isNaN(num)) return "\u2014";
    if (num >= 1) {
        return num.toFixed(2) + " s";
    }
    var ms = Math.round(num * 1000);
    return ms + " ms";
}

function formatTimestamp(iso) {
    if (!iso) return "";
    try {
        var normalized = iso;
        if (/[+-]\d{2}:\d{2}$/.test(normalized)) {
            normalized = normalized.replace(/[+-]\d{2}:\d{2}$/, "Z");
        } else if (!normalized.endsWith("Z")) {
            normalized = normalized + "Z";
        }
        var d = new Date(normalized);
        if (isNaN(d.getTime())) return iso;
        var day = d.getDate();
        var months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
        var month = months[d.getMonth()];
        var year = d.getFullYear();
        var hh = d.getHours().toString().padStart(2, "0");
        var mm = d.getMinutes().toString().padStart(2, "0");
        return day + " " + month + " " + year + ", " + hh + ":" + mm;
    } catch (e) {
        return iso;
    }
}

function escapeHtml(str) {
    if (!str) return "";
    var result = "";
    for (var i = 0; i < str.length; i++) {
        var ch = str.charAt(i);
        switch (ch) {
            case "&": result += String.fromCharCode(38) + "amp;" + String.fromCharCode(59); break;
            case "<": result += String.fromCharCode(60) + "lt;" + String.fromCharCode(59); break;
            case ">": result += String.fromCharCode(62) + "gt;" + String.fromCharCode(59); break;
            case '"': result += String.fromCharCode(34) + "quot;" + String.fromCharCode(59); break;
            case "'": result += String.fromCharCode(39) + "39;" + String.fromCharCode(59); break;
            default: result += ch;
        }
    }
    return result;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
var allRuns = [];
var filteredRuns = [];

// ---------------------------------------------------------------------------
// Load results from backend
// ---------------------------------------------------------------------------

async function loadResults() {
    try {
        showStatus("Loading results\u2026", "info");
        var resp = await fetch("/api/results");
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        var data = await resp.json();
        allRuns = data.results || [];
        showStatus("", "");
        applyFilters();
    } catch (e) {
        showStatus("Failed to load results: " + e.message, "error");
    }
}

// ---------------------------------------------------------------------------
// Filtering
// ---------------------------------------------------------------------------

function applyFilters() {
    var modelFilter = (filterModelInput.value || "").toLowerCase().trim();
    var hardwareFilter = (filterHardwareInput.value || "").toLowerCase().trim();
    var envFilter = filterEnvSelect.value;

    filteredRuns = allRuns.filter(function (run) {
        if (modelFilter) {
            var modelKey = (run.model_key || "").toLowerCase();
            var modelDisplay = (run.model_display_name || "").toLowerCase();
            if (modelKey.indexOf(modelFilter) === -1 && modelDisplay.indexOf(modelFilter) === -1) {
                return false;
            }
        }
        if (hardwareFilter) {
            var hw = (run.hardware_label || "").toLowerCase();
            if (hw.indexOf(hardwareFilter) === -1) {
                return false;
            }
        }
        if (envFilter) {
            var env = run.execution_environment || "";
            if (env !== envFilter) {
                return false;
            }
        }
        return true;
    });

    renderResults();
}

function clearFilters() {
    filterModelInput.value = "";
    filterHardwareInput.value = "";
    filterEnvSelect.value = "";
    applyFilters();
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderResults() {
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

        // Build delete button HTML
        var deleteBtnHtml = '<button class="delete-btn" title="Delete this run" data-run-id="' + escapeHtml(rid) + '" data-model="' + escapeHtml(group.model) + '" data-timestamp="' + escapeHtml(group.timestamp) + '">&#x1F5D1;</button>';

        header.innerHTML = '<span>Run #' + (idx + 1) + ' <span class="timestamp">(' + formatTimestamp(group.timestamp) + ')</span></span>' + badges + deleteBtnHtml;
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
        resultsContainer.appendChild(div);
    }

    // Render historical comparison chart
    renderHistoryCharts();
}

// ---------------------------------------------------------------------------
// SVG Charts (copied from dashboard.js, adapted for grouped data)
// ---------------------------------------------------------------------------

function renderHistoryCharts() {
    if (filteredRuns.length === 0) {
        chartsPanel.classList.add("hidden");
        return;
    }

    // Build grouped data for chart
    var groups = {};
    var order = [];
    for (var i = 0; i < filteredRuns.length; i++) {
        var run = filteredRuns[i];
        var rid = run.run_id || "";
        if (!rid) continue;
        if (!groups[rid]) {
            groups[rid] = { timestamp: run.timestamp || "", model: run.model_key || run.model_display_name || "", runs: [] };
            order.push(rid);
        }
        groups[rid].runs.push(run);
    }

    var runsWithWarm = [];
    for (var idx = 0; idx < order.length; idx++) {
        var rid = order[idx];
        var group = groups[rid];
        var warmRuns = group.runs.filter(function (r) { return r.cold_or_warm === "warm"; });
        if (warmRuns.length === 0) continue;

        var warmTps = warmRuns.map(function (r) { return parseFloat(r.tokens_per_second) || 0; }).filter(function (v) { return v > 0; });
        var warmTtfts = warmRuns.map(function (r) { return parseFloat(r.ttft_seconds) || 0; });

        if (warmTps.length === 0) continue;

        var avgTps = warmTps.reduce(function (a, b) { return a + b; }, 0) / warmTps.length;
        var avgTtft = warmTtfts.reduce(function (a, b) { return a + b; }, 0) / warmTtfts.length;

        runsWithWarm.push({
            id: rid,
            timestamp: group.timestamp,
            model: group.model,
            avgWarmTps: avgTps,
            avgWarmTtft: avgTtft,
            warmCount: warmTps.length
        });
    }

    if (runsWithWarm.length < 2) {
        chartsPanel.classList.add("hidden");
        return;
    }

    chartsPanel.classList.remove("hidden");
    chartsContainer.innerHTML = "";

    renderHistoricalComparisonChart(runsWithWarm);
}

function svgCreate(tag, attrs) {
    var el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    if (attrs) {
        for (var k in attrs) {
            if (Object.prototype.hasOwnProperty.call(attrs, k)) {
                el.setAttribute(k, attrs[k]);
            }
        }
    }
    return el;
}

function renderHistoricalComparisonChart(runsWithWarm) {
    var chartW = 520, chartH = 280;
    var margin = { top: 30, right: 30, bottom: 55, left: 70 };
    var w = chartW - margin.left - margin.right;
    var h = chartH - margin.top - margin.bottom;

    var maxTps = 0;
    for (var j = 0; j < runsWithWarm.length; j++) {
        if (runsWithWarm[j].avgWarmTps > maxTps) maxTps = runsWithWarm[j].avgWarmTps;
    }
    maxTps = maxTps * 1.15 || 100;

    var maxYTicks = 5;
    var yStep = maxTps / maxYTicks;
    if (yStep === 0) yStep = 10;
    var yMax = Math.ceil(maxTps / yStep) * yStep;

    var barGroupWidth = Math.min(60, (w / runsWithWarm.length) * 0.85);
    var barWidth = Math.max(12, (barGroupWidth / 3));

    var svg = svgCreate("svg", {
        width: chartW, height: chartH, viewBox: "0 0 " + chartW + " " + chartH,
        "aria-label": "Historical warm average comparison"
    });

    // Background
    svg.appendChild(svgCreate("rect", {
        x: margin.left, y: margin.top, width: w, height: h,
        fill: "#1a1a2e", rx: 4
    }));

    // Grid lines and Y labels
    for (var t = 0; t <= maxYTicks; t++) {
        var val = t * yStep;
        var gy = margin.top + h - (val / yMax) * h;
        svg.appendChild(svgCreate("line", {
            x1: margin.left, y1: gy, x2: margin.left + w, y2: gy,
            stroke: "#333", "stroke-width": 0.5
        }));
        var yLabel = svgCreate("text", {
            x: margin.left - 8, y: gy + 4,
            fill: "#999", "font-size": "10", "text-anchor": "end"
        });
        yLabel.textContent = val.toFixed(0);
        svg.appendChild(yLabel);
    }

    // Bars (grouped by run)
    for (var k = 0; k < runsWithWarm.length; k++) {
        var group = runsWithWarm[k];
        var groupX = margin.left + (k + 0.5) / runsWithWarm.length * w - barGroupWidth / 2;

        // TPS bar (green)
        var tpsH = (group.avgWarmTps / yMax) * h;
        svg.appendChild(svgCreate("rect", {
            x: groupX, y: margin.top + h - tpsH,
            width: barWidth, height: tpsH,
            fill: "#50d890", rx: 2, opacity: 0.85
        }));

        // TTFT bar (blue, scaled)
        var maxTtft = 0;
        for (var m = 0; m < runsWithWarm.length; m++) {
            if (runsWithWarm[m].avgWarmTtft > maxTtft) maxTtft = runsWithWarm[m].avgWarmTtft;
        }
        maxTtft = maxTtft * 1.15 || 1;
        var ttftH = (group.avgWarmTtft / maxTtft) * h * 0.5;
        svg.appendChild(svgCreate("rect", {
            x: groupX + barWidth + 2, y: margin.top + h - ttftH,
            width: barWidth, height: ttftH,
            fill: "#4a90d9", rx: 2, opacity: 0.85
        }));

        // Model label
        var label = group.model.length > 12 ? group.model.substring(0, 10) + "\u2026" : group.model;
        var xLabel = svgCreate("text", {
            x: groupX + barGroupWidth / 2, y: chartH - 10,
            fill: "#ccc", "font-size": "9", "text-anchor": "middle"
        });
        xLabel.textContent = label;
        svg.appendChild(xLabel);

        // Warm count
        var countLabel = svgCreate("text", {
            x: groupX + barGroupWidth / 2, y: chartH - 2,
            fill: "#999", "font-size": "8", "text-anchor": "middle"
        });
        countLabel.textContent = "(" + group.warmCount + ")";
        svg.appendChild(countLabel);
    }

    // Legend
    var legendY = 14;
    var legendItems = [
        { color: "#50d890", label: "Warm Avg tok/s" },
        { color: "#4a90d9", label: "Warm Avg TTFT" }
    ];
    var legendX = margin.left;
    for (var l = 0; l < legendItems.length; l++) {
        var item = legendItems[l];
        svg.appendChild(svgCreate("rect", {
            x: legendX, y: legendY - 4, width: 8, height: 8,
            fill: item.color, rx: 1
        }));
        var legText = svgCreate("text", {
            x: legendX + 11, y: legendY + 3,
            fill: "#ccc", "font-size": "10"
        });
        legText.textContent = item.label;
        svg.appendChild(legText);
        legendX += 14 + item.label.length * 6.5;
    }

    chartsContainer.appendChild(svg);
}

// ---------------------------------------------------------------------------
// Status display
// ---------------------------------------------------------------------------

function showStatus(msg, type) {
    // Simple status — could add a status element if needed
    if (!msg) return;
}

// ---------------------------------------------------------------------------
// Delete functionality
// ---------------------------------------------------------------------------

function openDeleteModal(runId, model, timestamp) {
    pendingDeleteRunId = runId;
    pendingDeleteModel = model;
    pendingDeleteTimestamp = timestamp;

    var tsDisplay = formatTimestamp(timestamp);
    deleteModalBody.innerHTML =
        '<p><strong>Model:</strong> ' + escapeHtml(model) + '</p>' +
        '<p><strong>Run ID:</strong> <code>' + escapeHtml(runId) + '</code></p>' +
        '<p><strong>Date:</strong> ' + tsDisplay + '</p>' +
        '<p style="margin-top:0.75rem;color:#dc2626;">This permanently removes this result.</p>';

    deleteModal.classList.remove("hidden");
}

function closeDeleteModal() {
    pendingDeleteRunId = null;
    pendingDeleteModel = "";
    pendingDeleteTimestamp = "";
    deleteModal.classList.add("hidden");
}

async function executeDelete() {
    if (!pendingDeleteRunId) {
        closeDeleteModal();
        return;
    }

    var runId = pendingDeleteRunId;
    closeDeleteModal();

    try {
        var resp = await fetch("/api/results/" + encodeURIComponent(runId), {
            method: "DELETE"
        });

        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }

        // Reload results after deletion
        await loadResults();
    } catch (e) {
        showStatus("Failed to delete run: " + e.message, "error");
    }
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------

filterModelInput.addEventListener("input", applyFilters);
filterHardwareInput.addEventListener("input", applyFilters);
filterEnvSelect.addEventListener("change", applyFilters);
clearFiltersBtn.addEventListener("click", clearFilters);

// Delete modal event listeners
cancelDeleteBtn.addEventListener("click", closeDeleteModal);
confirmDeleteBtn.addEventListener("click", executeDelete);

// Close modal on overlay click
if (deleteModal) {
    deleteModal.addEventListener("click", function (e) {
        if (e.target === deleteModal) {
            closeDeleteModal();
        }
    });
}

// Close modal on Escape key
document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !deleteModal.classList.contains("hidden")) {
        closeDeleteModal();
    }
});

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
// Init
// ---------------------------------------------------------------------------

loadResults();
