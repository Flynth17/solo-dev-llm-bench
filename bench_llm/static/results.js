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
var allTasks = [];

// Active view — default to "raw" (Raw Speed)
var activeView = "raw"; // "raw", "markdown", "python", "java", "unsolvable"

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

// Rendering
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
    // Only render chart when Raw Speed view is active
    if (activeView !== "raw") {
        return;
    }
    if (filteredRuns.length === 0) {
        chartsPanel.classList.add("hidden");
        return;
    }

    // Build grouped data for chart — one entry per run (not per model)
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
        if (warmTps.length === 0) continue;

        var avgTps = warmTps.reduce(function (a, b) { return a + b; }, 0) / warmTps.length;

        runsWithWarm.push({
            id: rid,
            timestamp: group.timestamp,
            model: group.model,
            avgWarmTps: avgTps,
            warmCount: warmTps.length
        });
    }

    if (runsWithWarm.length < 2) {
        chartsPanel.classList.add("hidden");
        return;
    }

    // Group by model name, find best per model, sort fastest → slowest
    var modelGroups = {};
    var modelOrder = [];
    for (var j = 0; j < runsWithWarm.length; j++) {
        var rw = runsWithWarm[j];
        var key = rw.model;
        if (!modelGroups[key]) {
            modelGroups[key] = { model: key, best: rw, others: [] };
            modelOrder.push(key);
        } else {
            // Track best
            if (rw.avgWarmTps > modelGroups[key].best.avgWarmTps) {
                modelGroups[key].others.push(modelGroups[key].best);
                modelGroups[key].best = rw;
            } else {
                modelGroups[key].others.push(rw);
            }
        }
    }

    // Sort others fastest → slowest within each group
    for (var m in modelGroups) {
        if (Object.prototype.hasOwnProperty.call(modelGroups, m)) {
            modelGroups[m].others.sort(function (a, b) { return b.avgWarmTps - a.avgWarmTps; });
        }
    }

    // Sort models by best avgWarmTps descending
    modelOrder.sort(function (a, b) { return modelGroups[b].best.avgWarmTps - modelGroups[a].best.avgWarmTps; });

    var groupedModels = [];
    for (var gi = 0; gi < modelOrder.length; gi++) {
        groupedModels.push(modelGroups[modelOrder[gi]]);
    }

    // Preserve expand state before clearing
    var expandedModels = getExpandedModels();

    chartsPanel.classList.remove("hidden");

    renderGroupedModelChart(groupedModels);

    // Restore expand state for models that were expanded
    restoreExpandedModels(expandedModels);
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

/* ---------------------------------------------------------------------------
   Grouped Model Chart — one row per model, expand/collapse
   --------------------------------------------------------------------------- */

function renderGroupedModelChart(groupedModels) {
    chartsContainer.innerHTML = "";

    // Compute global max TPS across all models' best results
    var maxTps = 0;
    for (var gi = 0; gi < groupedModels.length; gi++) {
        if (groupedModels[gi].best.avgWarmTps > maxTps) maxTps = groupedModels[gi].best.avgWarmTps;
    }
    maxTps = maxTps * 1.15 || 100;

    // Compute chart width from container so it fills the card on any screen
    var containerWidth = chartsContainer.clientWidth || 700;
    var margin = { top: 20, right: 100, bottom: 10, left: 200 };
    var chartW = containerWidth - margin.left - margin.right;
    // Ensure minimum width so bars and labels fit
    if (chartW < 400) chartW = 400;
    var chartH = 50 * groupedModels.length + 60;
    var rowHeight = 50;
    var w = chartW - margin.left - margin.right;
    var h = chartH - margin.top - margin.bottom;
    var maxXTicks = 6;
    var xStep = maxTps / maxXTicks;
    if (xStep === 0) xStep = 10;
    var magnitude = Math.pow(10, Math.floor(Math.log10(xStep)));
    var residual = xStep / magnitude;
    var niceXStep;
    if (residual <= 1.5) niceXStep = 1 * magnitude;
    else if (residual <= 3) niceXStep = 2 * magnitude;
    else if (residual <= 7) niceXStep = 5 * magnitude;
    else niceXStep = 10 * magnitude;
    var xMax = Math.ceil(maxTps / niceXStep) * niceXStep;

    // SVG background grid — pointer-events: none lets clicks pass through to model rows below
    var svg = svgCreate("svg", {
        width: "100%",
        height: chartH,
        viewBox: "0 0 " + chartW + " " + chartH,
        "aria-label": "Historical warm average tokens/sec comparison by model"
    });
    svg.style.width = "100%";
    svg.style.maxWidth = (chartW + margin.left + margin.right) + "px";
    svg.style.display = "block";
    svg.style.pointerEvents = "none";
    svg.appendChild(svgCreate("rect", {
        x: 0, y: 0, width: chartW, height: chartH, fill: "transparent"
    }));
    for (var t = 0; t <= maxXTicks; t++) {
        var val = t * niceXStep;
        var gx = margin.left + (val / xMax) * w;
        svg.appendChild(svgCreate("line", {
            x1: gx, y1: margin.top, x2: gx, y2: margin.top + h,
            stroke: "#333", "stroke-width": 0.5
        }));
        var xLabel = svgCreate("text", {
            x: gx, y: chartH - 2,
            fill: "#111", "font-size": "10", "text-anchor": "middle"
        });
        xLabel.textContent = val.toFixed(0);
        svg.appendChild(xLabel);
    }
    chartsContainer.appendChild(svg);

    // Model rows — best result first, expand/collapse for others
    for (var mi = 0; mi < groupedModels.length; mi++) {
        var gm = groupedModels[mi];
        var y = margin.top + mi * rowHeight;
        var barW = (gm.best.avgWarmTps / xMax) * w;
        var displayName = gm.model;
        var hasChildren = gm.others.length > 0;
        var rowId = "model-row-" + mi;
        var childrenId = "children-" + mi;

        // Model row div
        var modelRow = document.createElement("div");
        modelRow.className = "grouped-model-row";
        modelRow.id = rowId;

        // Header
        var header = document.createElement("div");
        header.className = "grouped-model-header";

        // Expand arrow (only if multi-run) — use data-* for delegated handler
        var arrow = document.createElement("span");
        arrow.className = "expand-arrow";
        arrow.textContent = "\u25B6"; // ▸
        if (hasChildren) {
            arrow.setAttribute("data-model-key", displayName);
        } else {
            arrow.style.visibility = "hidden";
        }

        // Model name with tooltip for full name
        var modelName = document.createElement("span");
        modelName.className = "grouped-model-name";
        modelName.textContent = displayName;
        modelName.title = displayName;

        // BEST badge
        var bestBadge = document.createElement("span");
        bestBadge.className = "best-badge";
        bestBadge.textContent = "BEST";

        // Best tok/s value
        var bestVal = document.createElement("span");
        bestVal.className = "grouped-model-best";
        bestVal.textContent = gm.best.avgWarmTps.toFixed(1) + " tok/s";

        header.appendChild(arrow);
        header.appendChild(modelName);
        header.appendChild(bestBadge);
        header.appendChild(bestVal);
        modelRow.appendChild(header);

        // SVG bar for this model's best result
        var barY = y + 8;
        // Track background
        svg.appendChild(svgCreate("rect", {
            x: margin.left, y: barY, width: w, height: 35,
            fill: "#1a1a2e", rx: 4, opacity: 0.5
        }));
        svg.appendChild(svgCreate("rect", {
            x: margin.left, y: barY, width: Math.max(barW, 4), height: 35,
            fill: "#50d890", rx: 4, opacity: 0.85
        }));
        // Model name label (left of bar) — dark text for readability
        var nameLabel = svgCreate("text", {
            x: margin.left - 8, y: y + 30,
            fill: "#111", "font-size": "11", "text-anchor": "end",
            "font-family": "monospace"
        });
        nameLabel.textContent = displayName;
        svg.appendChild(nameLabel);
        // Value label (right of bar) — dark text for readability
        var valLabel = svgCreate("text", {
            x: margin.left + Math.max(barW, 4) + 6, y: y + 30,
            fill: "#111", "font-size": "11", "font-family": "monospace"
        });
        valLabel.textContent = gm.best.avgWarmTps.toFixed(1) + " tok/s";
        svg.appendChild(valLabel);
        // Date below — dark text for readability
        var dateLabel = svgCreate("text", {
            x: margin.left - 8, y: y + 46,
            fill: "#111", "font-size": "9", "text-anchor": "end"
        });
        dateLabel.textContent = formatTimestamp(gm.best.timestamp);
        svg.appendChild(dateLabel);

        // Children container (hidden by default, lazily rendered)
        var childrenDiv = document.createElement("div");
        childrenDiv.id = childrenId;
        childrenDiv.className = "grouped-model-children";
        childrenDiv.style.display = "none";
        childrenDiv.setAttribute("data-model-index", mi);

        modelRow.appendChild(childrenDiv);
        chartsContainer.appendChild(modelRow);
    }

    // Legend
    var legendY = 14;
    var legendItems = [
        { color: "#50d890", label: "Warm Avg tok/s" }
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
            fill: "#111", "font-size": "10"
        });
        legText.textContent = item.label;
        svg.appendChild(legText);
        legendX += 14 + item.label.length * 6.5;
    }

    // Save global state for toggle
    _currentGroupedModels = groupedModels;
    _currentChartW = chartW;
    _currentMarginLeft = margin.left;
    _currentMarginRight = margin.right;
    _currentXMax = xMax;

    // Attach delegated handler so clicks work after re-render
    _initDelegatedExpand();
}

// ---------------------------------------------------------------------------
// Delegated expand/collapse handler (robust across re-renders)
// ---------------------------------------------------------------------------

function _initDelegatedExpand() {
    chartsContainer.removeEventListener("click", _onExpandClick);
    chartsContainer.addEventListener("click", _onExpandClick);
}

function _onExpandClick(event) {
    var toggle = event.target.closest(".expand-arrow");
    if (!toggle || !toggle.hasAttribute("data-model-key")) {
        return;
    }
    event.stopPropagation();
    var modelKey = toggle.getAttribute("data-model-key");
    _toggleByModelKey(modelKey);
}

function _toggleByModelKey(modelKey) {
    var rows = chartsContainer.querySelectorAll(".grouped-model-row");
    var foundIdx = -1;
    for (var ri = 0; ri < rows.length; ri++) {
        var arrow = rows[ri].querySelector(".expand-arrow[data-model-key]");
        if (arrow && arrow.getAttribute("data-model-key") === modelKey) {
            foundIdx = parseInt(rows[ri].id.replace("model-row-", ""), 10);
            break;
        }
    }
    if (foundIdx >= 0) {
        toggleModelRows(foundIdx);
    }
}

function toggleModelRows(modelIndex) {
    var mi = parseInt(modelIndex, 10);
    var childrenId = "children-" + mi;
    var rowId = "model-row-" + mi;
    var childrenDiv = document.getElementById(childrenId);
    var rowEl = document.getElementById(rowId);
    if (!childrenDiv || !rowEl) return;

    var arrow = rowEl.querySelector(".expand-arrow");
    var isHidden = childrenDiv.style.display === "none";
    childrenDiv.style.display = isHidden ? "block" : "none";
    if (arrow) {
        if (isHidden) {
            arrow.classList.add("expanded");
            arrow.textContent = "\u25C0"; // ◀
        } else {
            arrow.classList.remove("expanded");
            arrow.textContent = "\u25B6"; // ▸
        }
    }

    // Render child bars if showing and not yet rendered
    if (isHidden && childrenDiv.children.length === 0) {
        var gm = _currentGroupedModels[mi];
        if (gm) {
            var innerWidth = _currentChartW - _currentMarginLeft - _currentMarginRight;
            var xMax = _currentXMax;
            for (var ci = 0; ci < gm.others.length; ci++) {
                var child = gm.others[ci];
                var childBarW = (child.avgWarmTps / xMax) * innerWidth;
                var childRow = document.createElement("div");
                childRow.className = "grouped-child-row";
                childRow.innerHTML =
                    '<span style="display:inline-block;width:' + Math.max(childBarW, 4) + 'px;height:14px;background:#50d890;border-radius:2px;opacity:0.7;"></span>' +
                    '<span class="child-tok">' + child.avgWarmTps.toFixed(1) + ' tok/s</span>' +
                    '<span class="child-date">' + formatTimestamp(child.timestamp) + '</span>';
                childrenDiv.appendChild(childRow);
            }
        }
    }
}

// Global state for grouped models (simple reference for toggle)
var _currentGroupedModels = [];
var _currentChartW = 700;
var _currentMarginLeft = 200;
var _currentMarginRight = 100;
var _currentXMax = 100;

// ---------------------------------------------------------------------------
// Expand state preservation across re-renders
// ---------------------------------------------------------------------------

function getExpandedModels() {
    var expanded = [];
    var rows = chartsContainer.querySelectorAll(".grouped-model-row");
    for (var i = 0; i < rows.length; i++) {
        var childrenId = rows[i].id; // "model-row-N"
        var idx = childrenId.replace("model-row-", "");
        var childrenDiv = document.getElementById("children-" + idx);
        if (childrenDiv && childrenDiv.style.display !== "none") {
            expanded.push(idx);
        }
    }
    return expanded;
}

function restoreExpandedModels(expandedIndices) {
    for (var i = 0; i < expandedIndices.length; i++) {
        var idx = expandedIndices[i];
        var childrenDiv = document.getElementById("children-" + idx);
        var rowEl = document.getElementById("model-row-" + idx);
        if (!childrenDiv || !rowEl) continue;

        childrenDiv.style.display = "block";
        var arrow = rowEl.querySelector(".expand-arrow");
        if (arrow) {
            arrow.classList.add("expanded");
            arrow.textContent = "\u25C0"; // ◀
        }

        // Lazily render children (same logic as toggle)
        var mi = parseInt(idx, 10);
        var gm = _currentGroupedModels[mi];
        if (gm && childrenDiv.children.length === 0) {
            var innerWidth = _currentChartW - _currentMarginLeft - _currentMarginRight;
            var xMax = _currentXMax;
            for (var ci = 0; ci < gm.others.length; ci++) {
                var child = gm.others[ci];
                var childBarW = (child.avgWarmTps / xMax) * innerWidth;
                var childRow = document.createElement("div");
                childRow.className = "grouped-child-row";
                childRow.innerHTML =
                    '<span style="display:inline-block;width:' + Math.max(childBarW, 4) + 'px;height:14px;background:#50d890;border-radius:2px;opacity:0.7;"></span>' +
                    '<span class="child-tok">' + child.avgWarmTps.toFixed(1) + ' tok/s</span>' +
                    '<span class="child-date">' + formatTimestamp(child.timestamp) + '</span>';
                childrenDiv.appendChild(childRow);
            }
        }
    }
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
// Task History
// ---------------------------------------------------------------------------

async function loadTasks() {
    try {
        var url = "/api/tasks-with-results";
        if (activeTaskType !== "all") {
            url += "?task_type=" + encodeURIComponent(activeTaskType);
        }
        var resp = await fetch(url);
        if (!resp.ok) return;
        var data = await resp.json();
        allTasks = data.tasks || [];
    } catch (_) {
        allTasks = [];
    }
}

function renderTasks() {
    var container = document.getElementById("task-history-container");
    if (!container) return;
    container.innerHTML = "";
    var taskHistorySection = document.getElementById("task-history-section");
    var emptyEl = document.getElementById("task-history-empty");

    if (allTasks.length === 0) {
        if (taskHistorySection) taskHistorySection.classList.remove("visible");
        if (taskHistorySection) taskHistorySection.classList.add("hidden");
        if (emptyEl) emptyEl.classList.remove("hidden");
        return;
    }

    if (emptyEl) emptyEl.classList.add("hidden");
    if (taskHistorySection) {
        taskHistorySection.classList.remove("hidden");
        taskHistorySection.classList.add("visible");
    }

    for (var i = 0; i < allTasks.length; i++) {
        var t = allTasks[i];
        var taskType = t.task_type || "";
        var passed = t.passed;
        var score = t.score;
        var initialErrors = t.initial_errors;
        var finalErrors = t.final_errors;
        var errorsFixed = t.errors_fixed;
        var tps = t.tokens_per_second;
        var model = t.model || "";
        var timestamp = t.timestamp || t.created_at || "";

        var div = document.createElement("div");
        div.className = "task-history-row";
        div.setAttribute("data-task-id", t.task_id);
        div.setAttribute("data-run-id", t.id);

        // Name
        var nameSpan = document.createElement("span");
        nameSpan.className = "task-history-name";
        nameSpan.textContent = t.task_name || "Unnamed";
        nameSpan.title = t.task_name || "Unnamed";
        div.appendChild(nameSpan);

        // Type badge
        var typeSpan = document.createElement("span");
        typeSpan.className = "task-history-type " + taskType;
        typeSpan.textContent = taskType.toUpperCase();
        div.appendChild(typeSpan);

        // Model
        if (model) {
            var modelSpan = document.createElement("span");
            modelSpan.className = "badge badge-model";
            modelSpan.textContent = model;
            modelSpan.title = "Model";
            div.appendChild(modelSpan);
        }

        // PASS/FAIL badge — use the stored passed value from the validator/task runner
        var effectivePass = (passed !== null && passed !== undefined) ? passed : false;
        if (effectivePass) {
            var statusBadge = document.createElement("span");
            statusBadge.className = "task-history-score pass";
            statusBadge.textContent = "PASS";
            statusBadge.title = "Task passed";
            div.appendChild(statusBadge);
        }

        // Score as percentage (stored 0.0-1.0, displayed as 0-100%)
        if (score !== null && score !== undefined) {
            var scoreSpan = document.createElement("span");
            scoreSpan.className = "task-history-score " + (effectivePass ? "pass" : "fail");
            scoreSpan.textContent = (score * 100).toFixed(0) + "%";
            scoreSpan.title = "Score: " + score.toFixed(4) + " (stored)";
            div.appendChild(scoreSpan);
        }

        // TPS
        if (tps !== null && tps !== undefined) {
            var tpsSpan = document.createElement("span");
            tpsSpan.className = "task-history-tps";
            tpsSpan.textContent = fmt2(tps) + " tok/s";
            div.appendChild(tpsSpan);
        }

        // Timestamp
        var tsSpan = document.createElement("span");
        tsSpan.className = "task-history-timestamp";
        tsSpan.textContent = formatTimestamp(timestamp);
        div.appendChild(tsSpan);

        // Actions
        var actionsDiv = document.createElement("div");
        actionsDiv.className = "task-history-actions";

        var delBtn = document.createElement("button");
        delBtn.className = "delete-task-btn";
        delBtn.title = "Delete this task run";
        delBtn.textContent = "\u2716";
        delBtn.addEventListener("click", (function (runId) {
            return function () { deleteTaskRun(runId); };
        })(t.id));
        actionsDiv.appendChild(delBtn);

        div.appendChild(actionsDiv);
        container.appendChild(div);

        // Empty state messages for types with no results
        if (allTasks.length === 0 && activeTaskType !== "all" && activeTaskType !== "markdown") {
            var emptyMsg = "";
            if (activeTaskType === "python") emptyMsg = "No Python results yet.";
            else if (activeTaskType === "java") emptyMsg = "No Java results yet.";
            else if (activeTaskType === "unsolvable") emptyMsg = "No Unsolvable results yet.";
            if (emptyEl && emptyMsg) {
                emptyEl.textContent = emptyMsg;
                emptyEl.classList.remove("hidden");
            }
        }

        // Markdown result card (expandable detail)
        if (taskType === "markdown") {
            var card = document.createElement("div");
            card.className = "markdown-result-card" + (passed === false ? " fail" : "");
            card.style.display = "none";

            var header = document.createElement("div");
            header.className = "markdown-result-header";

            var passBadge = document.createElement("span");
            passBadge.className = passed ? "pass-badge" : "fail-badge";
            passBadge.textContent = passed ? "PASS" : "FAIL";

            var scoreVal = document.createElement("span");
            scoreVal.className = "markdown-result-score";
            scoreVal.textContent = "Score: " + (score !== null && score !== undefined ? (score * 100).toFixed(0) + "%" : "N/A");

            header.appendChild(passBadge);
            header.appendChild(scoreVal);
            card.appendChild(header);

            var body = document.createElement("div");
            body.className = "markdown-result-body";

            // Errors
            var errorsItem = document.createElement("div");
            errorsItem.className = "markdown-result-item";
            var errorsLabel = document.createElement("span");
            errorsLabel.className = "label";
            errorsLabel.textContent = "Errors";
            var errorsValue = document.createElement("span");
            errorsValue.className = "value markdown-result-errors";
            errorsValue.textContent = (initialErrors !== null && initialErrors !== undefined ? initialErrors : "\u2014") + " \u2192 " + (finalErrors !== null && finalErrors !== undefined ? finalErrors : "\u2014");
            errorsItem.appendChild(errorsLabel);
            errorsItem.appendChild(errorsValue);
            body.appendChild(errorsItem);

            // Errors fixed
            var fixedItem = document.createElement("div");
            fixedItem.className = "markdown-result-item";
            var fixedLabel = document.createElement("span");
            fixedLabel.className = "label";
            fixedLabel.textContent = "Errors fixed";
            var fixedValue = document.createElement("span");
            fixedValue.className = "value";
            fixedValue.textContent = errorsFixed !== null && errorsFixed !== undefined ? errorsFixed : "\u2014";
            fixedItem.appendChild(fixedLabel);
            fixedItem.appendChild(fixedValue);
            body.appendChild(fixedItem);

            // Output tokens
            var outputTokens = t.output_tokens;
            if (outputTokens !== null && outputTokens !== undefined) {
                var outItem = document.createElement("div");
                outItem.className = "markdown-result-item";
                var outLabel = document.createElement("span");
                outLabel.className = "label";
                outLabel.textContent = "Output tokens";
                var outVal = document.createElement("span");
                outVal.className = "value";
                outVal.textContent = outputTokens;
                outItem.appendChild(outLabel);
                outItem.appendChild(outVal);
                body.appendChild(outItem);
            }

            // Input tokens
            var inputTokens = t.input_tokens;
            if (inputTokens !== null && inputTokens !== undefined) {
                var inItem = document.createElement("div");
                inItem.className = "markdown-result-item";
                var inLabel = document.createElement("span");
                inLabel.className = "label";
                inLabel.textContent = "Input tokens";
                var inVal = document.createElement("span");
                inVal.className = "value";
                inVal.textContent = inputTokens;
                inItem.appendChild(inLabel);
                inItem.appendChild(inVal);
                body.appendChild(inItem);
            }

            // Wall time
            var wallTime = t.wall_time_seconds;
            if (wallTime !== null && wallTime !== undefined) {
                var wallItem = document.createElement("div");
                wallItem.className = "markdown-result-item";
                var wallLabel = document.createElement("span");
                wallLabel.className = "label";
                wallLabel.textContent = "Wall time";
                var wallVal = document.createElement("span");
                wallVal.className = "value";
                wallVal.textContent = fmt2(wallTime) + " s";
                wallItem.appendChild(wallLabel);
                wallItem.appendChild(wallVal);
                body.appendChild(wallItem);
            }

            card.appendChild(body);

            // Expand button
            var expandBtn = document.createElement("button");
            expandBtn.className = "markdown-result-expand";
            expandBtn.textContent = "\u25BC Details";
            (function (card, btn) {
                btn.addEventListener("click", function () {
                    var extra = card.querySelector(".markdown-result-extra");
                    if (extra) {
                        extra.classList.toggle("visible");
                        btn.textContent = extra.classList.contains("visible") ? "\u25B6 Details" : "\u25BC Details";
                    }
                });
            })(card, expandBtn);
            card.appendChild(expandBtn);

            // Extra details (TTFT)
            var extra = document.createElement("div");
            extra.className = "markdown-result-extra";
            var ttft = t.ttft_seconds;
            if (ttft !== null && ttft !== undefined) {
                var ttftItem = document.createElement("div");
                ttftItem.className = "markdown-result-item";
                var ttftLabel = document.createElement("span");
                ttftLabel.className = "label";
                ttftLabel.textContent = "TTFT";
                var ttftVal = document.createElement("span");
                ttftVal.className = "value";
                ttftVal.textContent = formatTtft(ttft);
                ttftItem.appendChild(ttftLabel);
                ttftItem.appendChild(ttftVal);
                extra.appendChild(ttftItem);
            }
            card.appendChild(extra);

            container.appendChild(card);
        }
    }
}

async function deleteTaskHistory(taskId) {
    if (!confirm("Delete this task?")) return;

    try {
        var resp = await fetch("/api/tasks/" + encodeURIComponent(taskId), {
            method: "DELETE"
        });
        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }
        // Reload
        allTasks = [];
        loadTasks().then(function () { renderTasks(); });
    } catch (e) {
        alert("Failed to delete task: " + e.message);
    }
}

async function deleteTaskRun(runId) {
    if (!confirm("Delete this task run?")) return;

    try {
        var resp = await fetch("/api/task-runs/" + encodeURIComponent(runId), {
            method: "DELETE"
        });
        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }
        // Reload
        allTasks = [];
        loadTasks().then(function () { renderTasks(); });
    } catch (e) {
        alert("Failed to delete task run: " + e.message);
    }
}

// ---------------------------------------------------------------------------
// Task-type filter
// ---------------------------------------------------------------------------

var taskTypeFilter = document.getElementById("task-type-filter");
var taskTypeBtns = taskTypeFilter ? taskTypeFilter.querySelectorAll(".task-type-btn") : [];

for (var bi = 0; bi < taskTypeBtns.length; bi++) {
    (function (btn) {
        btn.addEventListener("click", function () {
            // Update active state
            for (var j = 0; j < taskTypeBtns.length; j++) {
                taskTypeBtns[j].classList.remove("active");
            }
            btn.classList.add("active");
            activeTaskType = btn.getAttribute("data-type");
            // Reload and re-render
            allTasks = [];
            loadTasks().then(function () { renderTasks(); });
        });
    })(taskTypeBtns[bi]);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadResults();
