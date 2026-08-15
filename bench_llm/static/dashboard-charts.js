/** Solo Dev LLM Bench - Dashboard chart rendering logic. */

// ---------------------------------------------------------------------------
// SVG Chart helpers
// ---------------------------------------------------------------------------

/** Create a simple SVG element. */
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

/** Build a chart container div with SVG inside. */
function createChartContainer(title, id) {
    var container = document.createElement("div");
    container.className = "chart-container";
    container.id = id || "chart";

    var titleEl = document.createElement("h4");
    titleEl.textContent = title;
    titleEl.className = "chart-title";
    container.appendChild(titleEl);

    var svgWrap = document.createElement("div");
    svgWrap.className = "chart-svg-wrap";
    svgWrap.id = id ? id + "-svg" : "chart-svg";
    container.appendChild(svgWrap);

    return { container: container, svgWrap: svgWrap };
}

/**
 * Chart A: Tokens/sec by iteration.
 * Shows cold (iteration 1) and warm (iterations 2+) points with a line.
 * Fix 5: Auto-scale Y-axis around observed results instead of starting from zero.
 * Fix v1.0.2: Internal horizontal inset so first/last points don't clip.
 */
function renderTokensPerSecChart(runs) {
    if (!runs || runs.length === 0) return null;

    var validRuns = runs.filter(function (r) { return r.tokens_per_second > 0; });
    if (validRuns.length === 0) return null;

    var chartW = 500, chartH = 280;
    var margin = { top: 30, right: 30, bottom: 55, left: 70 };
    var w = chartW - margin.left - margin.right;
    var h = chartH - margin.top - margin.bottom;

    // Fix v1.0.2: Internal horizontal inset for data points so first/last points
    // are never on the plot boundary. Points are spread across [margin.left+inset, margin.right+inset].
    var pointInset = 18;
    var plotLeft = margin.left + pointInset;
    var plotRight = chartW - margin.right - pointInset;
    var plotWidth = plotRight - plotLeft;

    // Fix 5: Auto-scale around observed range (min and max)
    var minTps = Infinity, maxTps = -Infinity;
    for (var i = 0; i < validRuns.length; i++) {
        var v = validRuns[i].tokens_per_second;
        if (v < minTps) minTps = v;
        if (v > maxTps) maxTps = v;
    }
    // Ensure minTps is 0 only when all values are 0
    if (minTps <= 0) minTps = 0;
    
    // Add 10% padding on both sides, but ensure minimum range of 10
    var dataRange = maxTps - minTps;
    if (dataRange < 10) {
        dataRange = 10;
        // Center around the midpoint if range is too small
        minTps = Math.max(0, (maxTps + minTps) / 2 - 5);
        maxTps = minTps + 10;
    }
    var padding = Math.max(dataRange * 0.10, 5);
    minTps = Math.max(0, minTps - padding);
    maxTps = maxTps + padding;

    var yMax = maxTps;
    var yMin = minTps;
    
    // Calculate nice Y-axis ticks
    var yRange = yMax - yMin;
    var maxYTicks = 5;
    var yStepRaw = yRange / maxYTicks;
    // Round yStep to a nice number
    var magnitude = Math.pow(10, Math.floor(Math.log10(yStepRaw)));
    var residual = yStepRaw / magnitude;
    var niceStep;
    if (residual <= 1.5) niceStep = 1 * magnitude;
    else if (residual <= 3) niceStep = 2 * magnitude;
    else if (residual <= 7) niceStep = 5 * magnitude;
    else niceStep = 10 * magnitude;
    
    var yMaxNice = Math.ceil(yMax / niceStep) * niceStep;
    var yMinNice = Math.floor(yMin / niceStep) * niceStep;

    var points = [];
    for (var j = 0; j < validRuns.length; j++) {
        var r = validRuns[j];
        // Fix v1.0.2: Distribute points across the inset plot area [plotLeft, plotRight]
        var totalSlots = runs.length - 1;
        var slotWidth = totalSlots > 0 ? plotWidth / totalSlots : 0;
        var x = plotLeft + (r.iteration - 1) * slotWidth;
        // Fix 5: Scale Y position around observed range
        var y = margin.top + h - ((r.tokens_per_second - yMin) / (yMax - yMin)) * h;
        points.push({ x: x, y: y, run: r });
    }

    var svg = svgCreate("svg", {
        width: chartW, height: chartH, viewBox: "0 0 " + chartW + " " + chartH,
        "aria-label": "Tokens per second by iteration"
    });

    // Background
    svg.appendChild(svgCreate("rect", {
        x: margin.left, y: margin.top, width: w, height: h,
        fill: "#1a1a2e", rx: 4
    }));

    // Grid lines and Y labels (Fix 5: use auto-scaled range)
    for (var t = 0; t <= maxYTicks; t++) {
        var val = yMin + t * niceStep;
        var gy = margin.top + h - ((val - yMin) / (yMax - yMin)) * h;
        // Clamp gy to stay within chart area
        gy = Math.max(margin.top, Math.min(margin.top + h, gy));
        svg.appendChild(svgCreate("line", {
            x1: margin.left, y1: gy, x2: margin.left + w, y2: gy,
            stroke: "#333", "stroke-width": 0.5
        }));
        var yLabel = svgCreate("text", {
            x: margin.left - 8, y: gy + 4,
            fill: "#999", "font-size": "10", "text-anchor": "end"
        });
        yLabel.textContent = Math.round(val);
        svg.appendChild(yLabel);
    }

    // X labels (use same inset x-coordinates as points)
    for (var k = 0; k < runs.length; k++) {
        var r = runs[k];
        var totalSlots = runs.length - 1;
        var slotWidth = totalSlots > 0 ? plotWidth / totalSlots : 0;
        var px = plotLeft + (r.iteration - 1) * slotWidth;
        var xLabel = svgCreate("text", {
            x: px, y: chartH - 5,
            fill: "#999", "font-size": "10", "text-anchor": "middle"
        });
        xLabel.textContent = r.iteration;
        svg.appendChild(xLabel);
    }

    // Connecting line
    if (points.length > 1) {
        var pathD = "M " + points[0].x + " " + points[0].y;
        for (var p = 1; p < points.length; p++) {
            pathD += " L " + points[p].x + " " + points[p].y;
        }
        svg.appendChild(svgCreate("path", {
            d: pathD, fill: "none", stroke: "#4a90d9", "stroke-width": 2
        }));
    }

    // Data points
    for (var q = 0; q < points.length; q++) {
        var pt = points[q];
        var isCold = pt.run.cold_or_warm === "cold";
        var color = isCold ? "#f5a623" : "#50d890";
        var label = isCold ? "Cold" : "Warm";

        // Circle
        svg.appendChild(svgCreate("circle", {
            cx: pt.x, cy: pt.y, r: 5,
            fill: color, stroke: "#fff", "stroke-width": 1.5
        }));

        // Value label above point (shifted up to avoid overlap)
        var valLabel = svgCreate("text", {
            x: pt.x, y: pt.y - 14,
            fill: "#fff", "font-size": "10", "text-anchor": "middle"
        });
        valLabel.textContent = pt.run.tokens_per_second.toFixed(1);
        svg.appendChild(valLabel);

        // Cold/Warm badge below point (shifted down to avoid overlap with value label)
        var badgeY = pt.y + 14;
        var badgeRect = svgCreate("rect", {
            x: pt.x - 16, y: badgeY, width: 32, height: 14,
            fill: color, rx: 3, opacity: 0.8
        });
        svg.appendChild(badgeRect);
        var badgeText = svgCreate("text", {
            x: pt.x, y: badgeY + 10,
            fill: "#fff", "font-size": "8", "text-anchor": "middle"
        });
        badgeText.textContent = label;
        svg.appendChild(badgeText);
    }

    // Legend
    var legendY = 12;
    var legendItems = [
        { color: "#f5a623", label: "Cold (iter 1)" },
        { color: "#50d890", label: "Warm (iter 2+)" }
    ];
    var legendX = margin.left;
    for (var l = 0; l < legendItems.length; l++) {
        var item = legendItems[l];
        svg.appendChild(svgCreate("circle", {
            cx: legendX + 5, cy: legendY, r: 4, fill: item.color
        }));
        var legText = svgCreate("text", {
            x: legendX + 12, y: legendY + 4,
            fill: "#ccc", "font-size": "10"
        });
        legText.textContent = item.label;
        svg.appendChild(legText);
        legendX += 12 + item.label.length * 6.5;
    }

    var wrap = createChartContainer("Tokens/sec by Iteration", "tps-chart");
    wrap.svgWrap.appendChild(svg);
    return wrap.container;
}

/**
 * Chart B: TTFT by iteration.
 * Uses bar chart since TTFT values are typically small.
 * Fix 6: Increased margins to prevent Cold/Warm badge clipping.
 */
function renderTtftChart(runs) {
    if (!runs || runs.length === 0) return null;

    var validRuns = runs.filter(function (r) { return r.ttft_seconds > 0; });
    if (validRuns.length === 0) return null;

    var chartW = 500, chartH = 280;
    // Fix 6: Increased margins (bottom from 40->55, left from 60->70, top from 20->30)
    var margin = { top: 30, right: 30, bottom: 55, left: 70 };
    var w = chartW - margin.left - margin.right;
    var h = chartH - margin.top - margin.bottom;

    var maxTtft = 0;
    for (var i = 0; i < validRuns.length; i++) {
        var v = parseFloat(validRuns[i].ttft_seconds) || 0;
        if (v > maxTtft) maxTtft = v;
    }
    maxTtft = maxTtft * 1.15 || 1;

    var maxYTicks = 5;
    var yStep = maxTtft / maxYTicks;
    if (yStep === 0) yStep = 0.1;
    var yMax = Math.ceil(maxTtft / yStep) * yStep;

    var barWidth = Math.min(40, (w / runs.length) * 0.7);

    var svg = svgCreate("svg", {
        width: chartW, height: chartH, viewBox: "0 0 " + chartW + " " + chartH,
        "aria-label": "TTFT by iteration"
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
        yLabel.textContent = val.toFixed(2);
        svg.appendChild(yLabel);
    }

    // X labels
    for (var k = 0; k < runs.length; k++) {
        var r = runs[k];
        var px = margin.left + (r.iteration - 0.5) / runs.length * w;
        var xLabel = svgCreate("text", {
            x: px, y: chartH - 5,
            fill: "#999", "font-size": "10", "text-anchor": "middle"
        });
        xLabel.textContent = r.iteration;
        svg.appendChild(xLabel);
    }

    // Bars
    for (var j = 0; j < runs.length; j++) {
        var r = runs[j];
        var val = parseFloat(r.ttft_seconds) || 0;
        var barH = (val / yMax) * h;
        var x = margin.left + (j + 0.5) / runs.length * w - barWidth / 2;
        var y = margin.top + h - barH;
        var isCold = r.cold_or_warm === "cold";
        var color = isCold ? "#f5a623" : "#50d890";

        svg.appendChild(svgCreate("rect", {
            x: x, y: y, width: barWidth, height: barH,
            fill: color, rx: 3, opacity: 0.85
        }));

        // Value label on top
        var valLabel = svgCreate("text", {
            x: x + barWidth / 2, y: y - 4,
            fill: "#fff", "font-size": "10", "text-anchor": "middle"
        });
        valLabel.textContent = val.toFixed(2);
        svg.appendChild(valLabel);

        // Cold/Warm badge below
        var badgeRect = svgCreate("rect", {
            x: x + barWidth / 2 - 14, y: chartH - 22, width: 28, height: 12,
            fill: color, rx: 2, opacity: 0.8
        });
        svg.appendChild(badgeRect);
        var badgeText = svgCreate("text", {
            x: x + barWidth / 2, y: chartH - 13,
            fill: "#fff", "font-size": "8", "text-anchor": "middle"
        });
        badgeText.textContent = isCold ? "Cold" : "Warm";
        svg.appendChild(badgeText);
    }

    // Legend
    var legendY = 12;
    var legendItems = [
        { color: "#f5a623", label: "Cold" },
        { color: "#50d890", label: "Warm" }
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

    var wrap = createChartContainer("TTFT by Iteration (seconds)", "ttft-chart");
    wrap.svgWrap.appendChild(svg);
    return wrap.container;
}

/**
 * Chart C: Historical comparison.
 * Shows warm avg tokens/sec and warm avg TTFT for each run.
 * Fix 6: Increased left margin to prevent Y-axis label clipping.
 */
function renderHistoricalComparison(groupedRuns) {
    if (!groupedRuns || groupedRuns.length === 0) return null;

    // Filter runs that have warm data
    var runsWithWarm = [];
    for (var i = 0; i < groupedRuns.length; i++) {
        var group = groupedRuns[i];
        var warmRuns = group.runs.filter(function (r) { return r.cold_or_warm === "warm"; });
        if (warmRuns.length === 0) continue;

        var warmTps = warmRuns.map(function (r) { return parseFloat(r.tokens_per_second) || 0; }).filter(function (v) { return v > 0; });
        var warmTtfts = warmRuns.map(function (r) { return parseFloat(r.ttft_seconds) || 0; });

        if (warmTps.length === 0) continue;

        var avgTps = warmTps.reduce(function (a, b) { return a + b; }, 0) / warmTps.length;
        var avgTtft = warmTtfts.reduce(function (a, b) { return a + b; }, 0) / warmTtfts.length;

        runsWithWarm.push({
            id: group.id || "",
            timestamp: group.timestamp || "",
            model: group.model || "Unknown",
            avgWarmTps: avgTps,
            avgWarmTtft: avgTtft,
            warmCount: warmTps.length
        });
    }

    if (runsWithWarm.length === 0) return null;

    var chartW = 520, chartH = 280;
    // Fix 6: Increased left margin from 60->70 to prevent Y-axis label clipping
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
        var ttftH = (group.avgWarmTtft / maxTtft) * h * 0.5; // Scale TTFT to half height
        svg.appendChild(svgCreate("rect", {
            x: groupX + barWidth + 2, y: margin.top + h - ttftH,
            width: barWidth, height: ttftH,
            fill: "#4a90d9", rx: 2, opacity: 0.85
        }));

        // Model label below
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

    var wrap = createChartContainer("Historical Comparison (Warm Averages)", "history-chart");
    wrap.svgWrap.appendChild(svg);
    return wrap.container;
}

/** Render charts for a single benchmark run. */
function renderResultsCharts(runs) {
    var chartsDiv = document.getElementById("results-charts");
    if (!chartsDiv) return;
    chartsDiv.innerHTML = "";

    if (!runs || runs.length < 2) return;

    // TPS chart
    var tpsChart = renderTokensPerSecChart(runs);
    if (tpsChart) chartsDiv.appendChild(tpsChart);

    // TTFT chart
    var ttftChart = renderTtftChart(runs);
    if (ttftChart) chartsDiv.appendChild(ttftChart);
}