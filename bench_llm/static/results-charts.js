/** Solo Dev LLM Bench - Historical Comparison chart logic (extracted from results.js). */

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

// ---------------------------------------------------------------------------
// Correctness Comparison Chart — grouped model bars (score × 100 = % PASS)
// Used for Markdown / Python / Java / Unsolvable tabs.
// Metric-specific: score-based, NOT tok/s.
// ---------------------------------------------------------------------------

function renderCorrectnessComparisonChart(taskRuns) {
    // Only render when a correctness tab is active
    if (activeView === "raw") return;
    if (!taskRuns || taskRuns.length === 0) {
        chartsPanel.classList.add("hidden");
        return;
    }

    // Group by model: keep only the most recent run per model.
    var modelMap = {};
    for (var i = 0; i < taskRuns.length; i++) {
        var r = taskRuns[i];
        var mk = r.model || "(unknown)";
        if (!modelMap[mk]) {
            modelMap[mk] = r;
        } else {
            // Keep the newer run (task_runs are newest-first, but compare timestamps explicitly).
            if ((r.timestamp || "") > (modelMap[mk].timestamp || "")) {
                modelMap[mk] = r;
            }
        }
    }

    var models = [];
    for (var k in modelMap) {
        if (Object.prototype.hasOwnProperty.call(modelMap, k)) {
            models.push({ model: k, run: modelMap[k] });
        }
    }

    if (models.length === 0) {
        chartsPanel.classList.add("hidden");
        return;
    }

    // Determine score × 100 for each model.
    var maxScore = 0;
    for (var mi2 = 0; mi2 < models.length; mi2++) {
        var s = Math.round((models[mi2].run.score || 0) * 100);
        if (s > maxScore) maxScore = s;
    }

    // Sort: score desc, then timestamp desc, then model name asc.
    models.sort(function (a, b) {
        var sa = Math.round((a.run.score || 0) * 100);
        var sb = Math.round((b.run.score || 0) * 100);
        if (sa !== sb) return sb - sa; // descending score
        if ((a.run.timestamp || "") > (b.run.timestamp || "")) return -1;
        if ((a.run.timestamp || "") < (b.run.timestamp || "")) return 1;
        var na = a.model.toLowerCase();
        var nb = b.model.toLowerCase();
        if (na < nb) return -1;
        if (na > nb) return 1;
        return 0;
    });

    // Determine tied top score for BEST treatment.
    var topScore = models.length > 0 ? Math.round((models[0].run.score || 0) * 100) : 0;
    var bestCount = 0;
    for (var bi = 0; bi < models.length; bi++) {
        if (Math.round((models[bi].run.score || 0) * 100) === topScore) bestCount++;
    }

    chartsPanel.classList.remove("hidden");
    renderCorrectnessChartRows(models, maxScore, topScore, bestCount);
}

function renderCorrectnessChartRows(models, maxScore, topScore, bestCount) {
    var container = document.getElementById("charts-container");
    if (!container) return;
    container.innerHTML = "";

    // Compute chart dimensions from container width.
    var containerWidth = container.clientWidth || 700;
    var margin = { top: 20, right: 100, bottom: 10, left: Math.max(200, containerWidth * 0.22) };
    var chartW = containerWidth - margin.left - margin.right;
    if (chartW < 400) chartW = 400;
    var rowH = 50;
    var h = Math.max(rowH * models.length + 60, rowH + 120);

    // SVG background grid — pointer-events: none passes clicks to model rows.
    var svg = svgCreate("svg", {
        width: "100%",
        height: h,
        viewBox: "0 0 " + (chartW + margin.left + margin.right) + " " + h,
        "aria-label": "Historical correctness comparison by model (% PASS)"
    });
    svg.style.width = "100%";
    svg.style.maxWidth = (chartW + margin.left + margin.right) + "px";
    svg.style.display = "block";
    svg.style.pointerEvents = "none";
    // Background grid lines at 0%, 25%, 50%, 75%, 100%.
    svg.appendChild(svgCreate("rect", {
        x: margin.left, y: margin.top, width: chartW, height: rowH * models.length + 40,
        fill: "transparent"
    }));
    var ticks = [0, 25, 50, 75, 100];
    for (var t = 0; t < ticks.length; t++) {
        var val = ticks[t];
        var gx = margin.left + (val / 100) * chartW;
        svg.appendChild(svgCreate("line", {
            x1: gx, y1: margin.top, x2: gx, y2: margin.top + rowH * models.length + 40,
            stroke: "#333", "stroke-width": val === 0 ? 1 : 0.5
        }));
        var xLabel = svgCreate("text", {
            x: gx, y: h - 2,
            fill: "#111", "font-size": "10", "text-anchor": "middle"
        });
        xLabel.textContent = val + "%";
        svg.appendChild(xLabel);
    }
    container.appendChild(svg);

    // Model rows — sorted highest score first.
    for (var mi3 = 0; mi3 < models.length; mi3++) {
        var m = models[mi3];
        var y = margin.top + mi3 * rowH;
        var barH = 35;
        var displayScore = Math.round((m.run.score || 0) * 100);
        var barW = (displayScore / 100) * chartW;

        // Model name label (left of bar).
        var nameLabel = svgCreate("text", {
            x: margin.left - 8, y: y + 28,
            fill: "#111", "font-size": "11", "text-anchor": "end",
            "font-family": "monospace"
        });
        nameLabel.textContent = m.model;
        svg.appendChild(nameLabel);

        // Bar track.
        svg.appendChild(svgCreate("rect", {
            x: margin.left, y: y + 5, width: chartW, height: barH,
            fill: "#1a1a2e", rx: 4, opacity: 0.5
        }));
        // Bar fill.
        svg.appendChild(svgCreate("rect", {
            x: margin.left, y: y + 5, width: Math.max(barW, 4), height: barH,
            fill: "#50d890", rx: 4, opacity: 0.85
        }));

        // Value label (right of bar).
        var valLabel = svgCreate("text", {
            x: margin.left + Math.max(barW, 4) + 6, y: y + 28,
            fill: "#111", "font-size": "11", "font-family": "monospace"
        });
        valLabel.textContent = displayScore + "% PASS";
        svg.appendChild(valLabel);

        // Date below.
        var dateLabel = svgCreate("text", {
            x: margin.left - 8, y: y + 46,
            fill: "#111", "font-size": "9", "text-anchor": "end"
        });
        dateLabel.textContent = formatTimestamp(m.run.timestamp);
        svg.appendChild(dateLabel);

        // BEST badge — SVG text near the bar end for tied top-scorers.
        if (displayScore === topScore && bestCount > 1) {
            var bestBadge = svgCreate("text", {
                x: margin.left + Math.max(barW, 4) - 46, y: y + 28,
                fill: "#FFD700", "font-size": "9", "font-family": "monospace",
                "font-weight": "bold"
            });
            bestBadge.textContent = "★ BEST";
            svg.appendChild(bestBadge);
        } else if (displayScore === topScore && bestCount === 1) {
            var bestBadge2 = svgCreate("text", {
                x: margin.left + Math.max(barW, 4) - 46, y: y + 28,
                fill: "#FFD700", "font-size": "9", "font-family": "monospace",
                "font-weight": "bold"
            });
            bestBadge2.textContent = "★ BEST";
            svg.appendChild(bestBadge2);
        }

        // Model row div for future expand/collapse.
        var modelRow = document.createElement("div");
        modelRow.className = "grouped-model-row";

        // Header with badges.
        var header = document.createElement("div");
        header.className = "grouped-model-header";

        // Expand arrow (hidden by default, will be visible when expand/collapse is implemented).
        var arrow = document.createElement("span");
        arrow.className = "expand-arrow";
        arrow.textContent = "\u25B6"; // ▸
        if (bestCount > 1) {
            // Multi-BEST: show arrow for future expand functionality.
            arrow.setAttribute("data-model-key", m.model);
        } else {
            arrow.style.visibility = "hidden";
        }

        // Model name with tooltip.
        var modelName = document.createElement("span");
        modelName.className = "grouped-model-name";
        modelName.textContent = m.model;
        modelName.title = m.model;

        // BEST badge (only for top scorers).
        var bestBadge = null;
        if (displayScore === topScore) {
            bestBadge = document.createElement("span");
            bestBadge.className = "best-badge";
            bestBadge.textContent = "BEST";
        }

        // Score value.
        var scoreVal = document.createElement("span");
        scoreVal.className = "grouped-model-best";
        scoreVal.textContent = displayScore + "% PASS";

        header.appendChild(arrow);
        header.appendChild(modelName);
        if (bestBadge) {
            header.appendChild(bestBadge);
        }
        header.appendChild(scoreVal);
        modelRow.appendChild(header);

        // Children container for future expand/collapse.
        var childrenDiv = document.createElement("div");
        childrenDiv.id = "children-" + mi3;
        childrenDiv.className = "grouped-model-children";
        childrenDiv.style.display = "none";
        childrenDiv.setAttribute("data-model-index", mi3);

        modelRow.appendChild(childrenDiv);
        container.appendChild(modelRow);
    }

    // Legend.
    var legendY = 14;
    var legendItems = [
        { color: "#50d890", label: "% PASS (latest run)" },
        { color: "#FFD700", label: "★ BEST" }
    ];
    var legendX = margin.left;
    for (var l2 = 0; l2 < legendItems.length; l2++) {
        var item2 = legendItems[l2];
        svg.appendChild(svgCreate("rect", {
            x: legendX, y: legendY - 4, width: 8, height: 8,
            fill: item2.color, rx: 1
        }));
        var legText = svgCreate("text", {
            x: legendX + 11, y: legendY + 3,
            fill: "#111", "font-size": "10"
        });
        legText.textContent = item2.label;
        svg.appendChild(legText);
        legendX += 14 + item2.label.length * 6.5;
    }

    // Save state for future expand/collapse.
    _currentGroupedModels = models.map(function (m, idx) { return ({ model: m.model, best: m.run, others: [] }); });
    _currentChartW = chartW;
    _currentMarginLeft = margin.left;
    _currentMarginRight = margin.right;
    _currentXMax = 100;

    // Attach delegated handler.
    _initDelegatedExpand();
}
