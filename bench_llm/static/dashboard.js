/** Solo Dev LLM Bench - dashboard client-side logic. */

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
var executionEnvSelect = document.getElementById("execution-env");
var connectionRow = document.getElementById("connection-row");
var connectionTypeSelect = document.getElementById("connection-type");
var hardwareLabelInput = document.getElementById("hardware-label");
var lmStudioUrlInput = document.getElementById("lm-studio-url");
var modelSelect = document.getElementById("model-select");
var refreshModelsBtn = document.getElementById("refresh-models");
var promptPresetSelect = document.getElementById("prompt-preset");
var savePromptBtn = document.getElementById("save-prompt-btn");
var renamePromptBtn = document.getElementById("rename-prompt-btn");
var deletePromptBtn = document.getElementById("delete-prompt-btn");
var promptInput = document.getElementById("prompt");
var iterationsInput = document.getElementById("iterations");
var maxTokensInput = document.getElementById("max-tokens");
var temperatureInput = document.getElementById("temperature");
var runBtn = document.getElementById("run-benchmark");
var statusEl = document.getElementById("status");
var resultsPanel = document.getElementById("results-panel");
var resultsContainer = document.getElementById("results-container");

// Track the currently loaded preset name (empty if custom)
var currentPresetName = "";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function showStatus(msg, type) {
    statusEl.textContent = msg;
    statusEl.className = "status " + type;
    statusEl.classList.remove("hidden");
}

function clearStatus() {
    statusEl.textContent = "";
    statusEl.className = "status hidden";
}

function hideResults() {
    resultsPanel.classList.add("hidden");
    resultsContainer.innerHTML = "";
}

function hideHistory() {
    historyPanel.classList.add("hidden");
    historyContainer.innerHTML = "";
}

function disableRun(disabled) {
    runBtn.disabled = disabled;
    refreshModelsBtn.disabled = disabled;
    if (disabled) {
        runBtn.textContent = "Running\u2026";
    } else {
        runBtn.textContent = "Run Benchmark";
    }
}

/** Format a number to 2 decimal places for display. */
function fmt2(value) {
    if (value === null || value === undefined || value === "") return "\u2014";
    var num = parseFloat(value);
    if (isNaN(num)) return "\u2014";
    return num.toFixed(2);
}

/** Format a timestamp for friendly display.
 *  Converts ISO timestamps to "10 Aug 2026, 19:56" format.
 *  Fix v1.0.2: Handle Python ISO format with any timezone suffix (+00:00, -05:00, etc). */
function formatTimestamp(iso) {
    if (!iso) return "";
    try {
        // Normalize timezone suffix so Date() can parse it reliably
        var normalized = iso;
        // Replace any trailing +HH:MM or -HH:MM timezone offset with Z
        // This handles Python's datetime.isoformat() output like 2026-08-10T19:22:08.817702+00:00
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

/** Format a number as integer for display (Fix 3: token counts). */
function fmtInt(value) {
    if (value === null || value === undefined || value === "") return "\u2014";
    var num = parseInt(value, 10);
    if (isNaN(num)) return "\u2014";
    return num.toString();
}

/** Format TTFT value: seconds if >= 1s, milliseconds if < 1s (Fix 7). */
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

// ---------------------------------------------------------------------------
// Execution Environment toggle
// ---------------------------------------------------------------------------

executionEnvSelect.addEventListener("change", function () {
    if (this.value === "Self-hosted") {
        connectionRow.classList.remove("hidden");
        connectionTypeSelect.disabled = false;
    } else {
        connectionRow.classList.add("hidden");
        connectionTypeSelect.disabled = true;
        connectionTypeSelect.value = "";
    }
});

// ---------------------------------------------------------------------------
// Load initial config
// ---------------------------------------------------------------------------

async function loadConfig() {
    try {
        var resp = await fetch("/api/config");
        var config = await resp.json();
        lmStudioUrlInput.value = config.lm_studio_url || "http://localhost:1234";
        promptInput.value = config.prompt || "";
        iterationsInput.value = config.iterations || 5;
        maxTokensInput.value = config.max_tokens || 500;
        temperatureInput.value = config.temperature != null ? config.temperature : 0;
        // New fields
        if (config.hardware_label) {
            hardwareLabelInput.value = config.hardware_label;
        }
        if (config.execution_environment) {
            executionEnvSelect.value = config.execution_environment;
            executionEnvSelect.dispatchEvent(new Event("change"));
        }
    } catch (_) {
        // Ignore - use defaults
    }
}

// ---------------------------------------------------------------------------
// Load models
// ---------------------------------------------------------------------------

async function loadModels() {
    var url = lmStudioUrlInput.value.replace(/\/$/, "");
    showStatus("Fetching model list from LM Studio\u2026", "info");
    try {
        var resp = await fetch("/api/models");
        if (!resp.ok) {
            throw new Error("HTTP " + resp.status);
        }
        var data = await resp.json();
        var models = data.models || [];

        modelSelect.innerHTML = '<option value="">\u2014 Select a model \u2014</option>';
        for (var i = 0; i < models.length; i++) {
            var m = models[i];
            var opt = document.createElement("option");
            opt.value = m.key;
            opt.textContent = m.name || m.key;
            if (m.quantization) {
                // Fix 1: Handle object quantization metadata safely
                var quantName = m.quantization;
                if (typeof quantName === "object" && quantName !== null) {
                    // Try multiple property names that LM Studio might use
                    quantName = quantName.name || quantName.display_name || quantName.quant || null;
                    // If still no name, try to extract something useful
                    if (!quantName) {
                        // Try to get the first meaningful string value
                        var keys = Object.keys(quantName);
                        for (var ki = 0; ki < keys.length; ki++) {
                            var v = quantName[keys[ki]];
                            if (typeof v === "string" && v) {
                                quantName = v;
                                break;
                            }
                        }
                    }
                    // Final fallback: don't show anything if we can't parse it
                    if (typeof quantName === "object") {
                        quantName = null;
                    }
                }
                if (quantName) {
                    opt.textContent += " (" + quantName + ")";
                }
            }
            modelSelect.appendChild(opt);
        }

        if (models.length === 0) {
            showStatus("No LLM models found. Load a model in LM Studio first.", "error");
        } else {
            clearStatus();
        }
    } catch (e) {
        showStatus("Failed to fetch models: " + e.message, "error");
    }
}

refreshModelsBtn.addEventListener("click", loadModels);

// ---------------------------------------------------------------------------
// Load & display results
// ---------------------------------------------------------------------------

async function loadResults() {
    try {
        var resp = await fetch("/api/benchmark/results");
        var data = await resp.json();
        var allRuns = data.results || [];
        renderHistory(allRuns);
    } catch (_) {
        hideHistory();
    }
}

/** HTML-escape a string safely using char codes to avoid encoding issues. */
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

/** Render a single benchmark run result. */
function renderRunResult(result, isLatest) {
    var group = document.createElement("div");
    group.className = "results-group";

    var label = isLatest ? "Latest Run" : "Run";

    // Build metadata badges
    var badgesHtml = '<div class="metadata">';
    badgesHtml += '<span class="badge badge-model">' + escapeHtml(result.model || "") + '</span>';
    if (result.hardware_label) {
        badgesHtml += '<span class="badge badge-hardware">' + escapeHtml(result.hardware_label) + '</span>';
    }
    if (result.execution_environment) {
        badgesHtml += '<span class="badge badge-env">' + escapeHtml(result.execution_environment) + '</span>';
    }
    if (result.connection_type && result.connection_type !== "None") {
        badgesHtml += '<span class="badge badge-conn">' + escapeHtml(result.connection_type) + '</span>';
    }
    badgesHtml += '</div>';

    // Fix v1.0.1: Use formatTimestamp for Latest Run timestamp (was showing raw ISO)
    var header = document.createElement("h3");
    header.innerHTML = label + ' \u2014 <code>' + escapeHtml(result.model || "") + '</code> <span class="timestamp">(' + formatTimestamp(result.timestamp) + ')</span>';

    // Overall aggregate
    var agg = result.aggregate || {};
    var summaryHtml = '<h4>Overall (all iterations)</h4>' +
        '<div class="aggregate">' +
            '<div class="aggregate-item"><div class="label">Avg</div><div class="value">' + fmt2(agg.avg_tokens_per_second) + ' tok/s</div></div>' +
            '<div class="aggregate-item"><div class="label">Min</div><div class="value">' + fmt2(agg.min_tokens_per_second) + ' tok/s</div></div>' +
            '<div class="aggregate-item"><div class="label">Max</div><div class="value">' + fmt2(agg.max_tokens_per_second) + ' tok/s</div></div>' +
        '</div>';

    // Warm aggregate
    var warmAgg = result.warm_aggregate || {};
    var warmHtml = "";
    if (warmAgg.available) {
        // Fix v1.0.1 screenshot: Warm TTFT uses formatTtft (shows ms for <1s)
        warmHtml = '<h4>Warm (iterations 2+)</h4>' +
            '<div class="warm-aggregate">' +
                '<div class="aggregate-item"><div class="label">Avg</div><div class="value">' + fmt2(warmAgg.avg_tokens_per_second) + ' tok/s</div></div>' +
                '<div class="aggregate-item"><div class="label">Avg TTFT</div><div class="value">' + formatTtft(warmAgg.avg_ttft) + '</div></div>' +
            '</div>';
    } else {
        warmHtml = '<h4>Warm (iterations 2+)</h4>' +
            '<div class="warm-aggregate"><span class="unavailable">Unavailable (only 1 iteration)</span></div>';
    }

    // Per-iteration table
    var table = document.createElement("table");
    table.innerHTML =
        '<thead><tr>' +
            '<th>Itr</th>' +
            '<th>Type</th>' +
            '<th>tok/s</th>' +
            '<th>TTFT</th>' +
            '<th>Input tokens</th>' +
            '<th>Output tokens</th>' +
            '<th>Wall (s)</th>' +
        '</tr></thead>' +
        '<tbody></tbody>';
    var tbody = table.querySelector("tbody");

    var runs = result.runs || [];
    for (var i = 0; i < runs.length; i++) {
        var r = runs[i];
        var tr = document.createElement("tr");
        var iterType = r.cold_or_warm || (r.iteration === 1 ? "cold" : "warm");
        tr.className = iterType === "cold" ? "cold-row" : "warm-row";
        tr.innerHTML =
            '<td>' + r.iteration + '</td>' +
            '<td>' + (iterType === "cold" ? '\u2744 Cold' : '\u2600 Warm') + '</td>' +
            '<td>' + fmt2(r.tokens_per_second) + '</td>' +
            '<td>' + formatTtft(r.ttft_seconds) + '</td>' +
            '<td>' + fmtInt(r.input_tokens) + '</td>' +
            '<td>' + fmtInt(r.output_tokens) + '</td>' +
            '<td>' + fmt2(r.wall_time_seconds) + '</td>';
        tbody.appendChild(tr);
    }

    group.appendChild(header);
    group.appendChild(document.createRange().createContextualFragment(badgesHtml));
    group.insertAdjacentHTML("beforeend", summaryHtml);
    group.insertAdjacentHTML("beforeend", warmHtml);
    group.appendChild(table);

    return group;
}


// ---------------------------------------------------------------------------
// Run benchmark
// ---------------------------------------------------------------------------

async function runBenchmark() {
    var model = modelSelect.value.trim();
    if (!model) {
        showStatus("Please select a model first.", "error");
        return;
    }

    // Determine prompt_name for CSV recording
    var presetName = promptPresetSelect.value;
    var promptName = "Custom";
    if (presetName) {
        // Check if the current prompt text matches the loaded preset exactly
        var matchedPreset = null;
        for (var i = 0; i < _cachedPrompts.length; i++) {
            if (_cachedPrompts[i].name === presetName) {
                matchedPreset = _cachedPrompts[i];
                break;
            }
        }
        if (matchedPreset && matchedPreset.prompt === promptInput.value) {
            promptName = presetName;
        } else if (matchedPreset) {
            promptName = presetName + " (modified)";
        } else {
            promptName = presetName;
        }
    }
    
    var config = {
        lm_studio_url: lmStudioUrlInput.value.replace(/\/$/, ""),
        model: model,
        prompt: promptInput.value,
        prompt_name: promptName,
        iterations: parseInt(iterationsInput.value, 10),
        max_tokens: parseInt(maxTokensInput.value, 10),
        temperature: parseFloat(temperatureInput.value),
        // New fields
        hardware_label: hardwareLabelInput.value.trim(),
        execution_environment: executionEnvSelect.value,
        connection_type: connectionTypeSelect.value,
    };

    disableRun(true);
    showStatus("Running benchmark\u2026", "info");
    hideResults();

    try {
        var resp = await fetch("/api/benchmark/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(config),
        });

        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }

        var data = await resp.json();
        clearStatus();

        // Display the new result
        resultsPanel.classList.remove("hidden");
        resultsContainer.innerHTML = "";

        var group = renderRunResult(data.result, true);
        resultsContainer.appendChild(group);

        // Render charts for this run
        renderResultsCharts(data.result.runs);

    } catch (e) {
        showStatus("Benchmark failed: " + e.message, "error");
    } finally {
        disableRun(false);
    }
}

runBtn.addEventListener("click", runBenchmark);

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

// ---------------------------------------------------------------------------
// Prompt preset management
// ---------------------------------------------------------------------------

/** Load saved prompts and populate the dropdown. */
async function loadPrompts() {
    try {
        var resp = await fetch("/api/prompts");
        if (!resp.ok) return;
        var data = await resp.json();
        var prompts = data.prompts || [];
        
        // Save current selection and prompt text
        var currentSelection = promptPresetSelect.value;
        var currentPromptText = promptInput.value;
        
        // Clear and rebuild dropdown
        promptPresetSelect.innerHTML = '<option value="">— Custom —</option>';
        for (var i = 0; i < prompts.length; i++) {
            var p = prompts[i];
            var opt = document.createElement("option");
            opt.value = p.name;
            opt.textContent = p.name;
            promptPresetSelect.appendChild(opt);
        }
        
        // Try to restore selection (exact match first, then case-insensitive)
        var restored = false;
        for (var j = 0; j < prompts.length; j++) {
            if (prompts[j].name === currentSelection) {
                promptPresetSelect.value = currentSelection;
                restored = true;
                break;
            }
        }
        if (!restored && currentSelection) {
            // Try case-insensitive match
            for (var k = 0; k < prompts.length; k++) {
                if (prompts[k].name.toLowerCase() === currentSelection.toLowerCase()) {
                    promptPresetSelect.value = prompts[k].name;
                    break;
                }
            }
        }
        
        // If a valid preset is selected, load its prompt text
        if (promptPresetSelect.value) {
            for (var m = 0; m < prompts.length; m++) {
                if (prompts[m].name === promptPresetSelect.value) {
                    promptInput.value = prompts[m].prompt;
                    currentPresetName = prompts[m].name;
                    return;
                }
            }
        }
        currentPresetName = "";
    } catch (_) {
        // Ignore - use defaults
    }
}

/** Determine the prompt_name to record for CSV based on current state. */
function determinePromptName() {
    var presetName = promptPresetSelect.value;
    if (!presetName) {
        return "Custom";
    }
    var loadedPrompt = currentPresetName;
    var currentPrompt = promptInput.value;
    // Check if the prompt matches the loaded preset exactly
    for (var i = 0; i < _cachedPrompts.length; i++) {
        if (_cachedPrompts[i].name === presetName && _cachedPrompts[i].prompt === currentPrompt) {
            return presetName;
        }
    }
    // Check if it's a modified preset
    for (var j = 0; j < _cachedPrompts.length; j++) {
        if (_cachedPrompts[j].name === presetName || _cachedPrompts[j].name === currentPresetName) {
            return presetName + " (modified)";
        }
    }
    return "Custom";
}

// Cache for prompts (used by determinePromptName)
var _cachedPrompts = [];

/** Reload cached prompts after any change. */
function refreshPromptsCache(callback) {
    fetch("/api/prompts")
        .then(function (r) { return r.json(); })
        .then(function (data) {
            _cachedPrompts = data.prompts || [];
            if (callback) callback();
        })
        .catch(function () {
            if (callback) callback();
        });
}

/** Populate dropdown and set initial state. */
async function initPromptPresets() {
    try {
        var resp = await fetch("/api/prompts");
        if (!resp.ok) return;
        var data = await resp.json();
        var prompts = data.prompts || [];
        _cachedPrompts = prompts;
        
        promptPresetSelect.innerHTML = '<option value="">— Custom —</option>';
        for (var i = 0; i < prompts.length; i++) {
            var p = prompts[i];
            var opt = document.createElement("option");
            opt.value = p.name;
            opt.textContent = p.name;
            promptPresetSelect.appendChild(opt);
        }
    } catch (_) {
        // Ignore
    }
}

/** Save current prompt textarea content as a new preset. */
async function savePrompt() {
    var promptText = promptInput.value;
    if (!promptText.trim()) {
        showStatus("Cannot save an empty prompt.", "error");
        return;
    }
    
    var name = promptInput.value.trim().split(/\s+/)[0];
    // Ask for name via prompt dialog
    var inputName = prompt("Enter a name for this prompt:", name.substring(0, 30));
    if (!inputName || !inputName.trim()) {
        return;
    }
    
    try {
        var resp = await fetch("/api/prompts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: inputName.trim(), prompt: promptText })
        });
        
        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            if (resp.status === 409) {
                showStatus(err.detail || "A prompt with this name already exists.", "error");
            } else {
                showStatus("Failed to save prompt.", "error");
            }
            return;
        }
        
        showStatus("Prompt saved successfully.", "info");
        refreshPromptsCache(function () {
            promptPresetSelect.value = inputName.trim();
            currentPresetName = inputName.trim();
        });
    } catch (e) {
        showStatus("Failed to save prompt: " + e.message, "error");
    }
}

/** Rename the currently selected prompt. */
async function renamePrompt() {
    var oldName = promptPresetSelect.value;
    if (!oldName) {
        showStatus("Please select a prompt to rename.", "error");
        return;
    }
    
    var newName = prompt("Enter a new name for '" + oldName + "':", oldName);
    if (!newName || !newName.trim() || newName.trim() === oldName) {
        return;
    }
    
    try {
        var resp = await fetch("/api/prompts/" + encodeURIComponent(oldName), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: newName.trim() })
        });
        
        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            if (resp.status === 404) {
                showStatus("Prompt '" + oldName + "' not found.", "error");
            } else if (resp.status === 409) {
                showStatus(err.detail || "A prompt with the new name already exists.", "error");
            } else {
                showStatus("Failed to rename prompt.", "error");
            }
            return;
        }
        
        showStatus("Prompt renamed to '" + newName.trim() + "'.", "info");
        refreshPromptsCache(function () {
            promptPresetSelect.value = newName.trim();
            currentPresetName = newName.trim();
        });
    } catch (e) {
        showStatus("Failed to rename prompt: " + e.message, "error");
    }
}

/** Delete the currently selected prompt. */
async function deletePrompt() {
    var name = promptPresetSelect.value;
    if (!name) {
        showStatus("Please select a prompt to delete.", "error");
        return;
    }
    
    if (!confirm("Delete the prompt '" + name + "'?")) {
        return;
    }
    
    try {
        var resp = await fetch("/api/prompts/" + encodeURIComponent(name), {
            method: "DELETE"
        });
        
        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            if (resp.status === 404) {
                showStatus("Prompt '" + name + "' not found.", "error");
            } else {
                showStatus("Failed to delete prompt.", "error");
            }
            return;
        }
        
        showStatus("Prompt '" + name + "' deleted.", "info");
        currentPresetName = "";
        refreshPromptsCache(function () {
            promptPresetSelect.value = "";
        });
    } catch (e) {
        showStatus("Failed to delete prompt: " + e.message, "error");
    }
}

// Prompt preset change handler
promptPresetSelect.addEventListener("change", function () {
    if (!this.value) {
        currentPresetName = "";
        return;
    }
    // Load the selected prompt's text
    for (var i = 0; i < _cachedPrompts.length; i++) {
        if (_cachedPrompts[i].name === this.value) {
            promptInput.value = _cachedPrompts[i].prompt;
            currentPresetName = _cachedPrompts[i].name;
            return;
        }
    }
});

savePromptBtn.addEventListener("click", savePrompt);
renamePromptBtn.addEventListener("click", renamePrompt);
deletePromptBtn.addEventListener("click", deletePrompt);

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadConfig().then(function () {
    // Auto-load models after config is loaded
    loadModels();
    // Initialize prompt presets
    initPromptPresets();
});
