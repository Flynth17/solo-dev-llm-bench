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
var iterationsInput = document.getElementById("iterations");
var maxTokensInput = document.getElementById("max-tokens");
var temperatureInput = document.getElementById("temperature");
var runBtn = document.getElementById("run-benchmark");
var statusEl = document.getElementById("status");
var resultsPanel = document.getElementById("results-panel");
var resultsContainer = document.getElementById("results-container");

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
maxTokensInput.value = config.max_tokens || 100000;
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
// Run Evaluation (speed tests only)
// ---------------------------------------------------------------------------

var runEvaluationBtn = document.getElementById("run-evaluation");

if (runEvaluationBtn) {
    runEvaluationBtn.addEventListener("click", runEvaluation);
}

async function runEvaluation() {
    var model = modelSelect.value.trim();
    if (!model) {
        showStatus("Please select a model first.", "error");
        return;
    }

    // Collect selected speed tests
    var speedTests = [];
    if (document.getElementById("eval-speed-small").checked) {
        speedTests.push("small");
    }
    if (document.getElementById("eval-speed-medium").checked) {
        speedTests.push("medium");
    }
    if (document.getElementById("eval-speed-large").checked) {
        speedTests.push("large");
    }

    // Collect selected correctness tests
    var correctnessTests = [];
    if (document.getElementById("eval-correctness-markdown").checked) {
        correctnessTests.push("markdown");
    }
    if (document.getElementById("eval-correctness-python").checked) {
        correctnessTests.push("python");
    }
    if (document.getElementById("eval-correctness-java").checked) {
        correctnessTests.push("java");
    }

    if (speedTests.length === 0 && correctnessTests.length === 0) {
        showStatus("Select at least one speed test or correctness test.", "error");
        return;
    }

    var config = {
        lm_studio_url: lmStudioUrlInput.value.replace(/\/$/, ""),
        model: model,
        execution_environment: executionEnvSelect.value,
        connection_type: connectionTypeSelect.value,
        hardware_label: hardwareLabelInput.value.trim(),
        iterations: parseInt(iterationsInput.value, 10),
        max_output_tokens: parseInt(maxTokensInput.value, 10),
        temperature: parseFloat(temperatureInput.value),
        speed_tests: speedTests,
        correctness_tests: correctnessTests,
    };

    disableRun(true);
    showStatus("Running evaluation\u2026", "info");
    hideResults();

    try {
        var resp = await fetch("/api/evaluation/run", {
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

        // Display evaluation results
        resultsPanel.classList.remove("hidden");
        resultsContainer.innerHTML = "";

        // Build summary
        var summary = data.summary || {};
        var summaryDiv = document.createElement("div");
        summaryDiv.className = "results-group";

        // Per-test correctness summary rows (data-driven, not hardcoded)
        var allCorrectnessResults = data.correctness_results || [];
        var passedCount = 0;
        var perTestSummaryHtml = "";
        for (var ci = 0; ci < allCorrectnessResults.length; ci++) {
            var cr = allCorrectnessResults[ci];
            var testPct = Math.round((cr.score || 0) * 100);
            // Always use PASS semantics: score represents percentage PASSED
            var testStatus = 'PASS';
            var testColor = cr.passed ? '#22c55e' : '#ef4444';
            if (cr.passed) {
                passedCount++;
            }
            perTestSummaryHtml +=
                '<div class="aggregate-item">' +
                    '<div class="label">' + escapeHtml(cr.test_label || cr.test_type).toUpperCase() + '</div>' +
                    '<div class="value" style="color:' + testColor + '; font-weight:bold;">' + testPct + '% ' + testStatus + '</div>' +
                '</div>';
        }

        // Tests Passed metric (only if there are correctness results)
        var totalCount = allCorrectnessResults.length;
        var testsPassedHtml = "";
        if (totalCount > 0) {
            testsPassedHtml =
                '<div class="aggregate-item">' +
                    '<div class="label">Tests Passed</div>' +
                    '<div class="value">' + passedCount + ' / ' + totalCount + '</div>' +
                '</div>';
        }

        summaryDiv.innerHTML =
            '<h3>Evaluation Summary</h3>' +
            '<div class="aggregate">' +
                '<div class="aggregate-item"><div class="label">Speed Tests</div><div class="value">' + (summary.speed_tests_run || 0) + '</div></div>' +
                '<div class="aggregate-item"><div class="label">Correctness Tests</div><div class="value">' + (summary.correctness_tests_run || 0) + '</div></div>' +
                '<div class="aggregate-item"><div class="label">Avg tok/s</div><div class="value">' + fmt2(summary.avg_tokens_per_second) + '</div></div>' +
                '<div class="aggregate-item"><div class="label">Avg TTFT</div><div class="value">' + formatTtft(summary.avg_ttft_seconds) + '</div></div>' +
                '<div class="aggregate-item"><div class="label">Total Wall (s)</div><div class="value">' + fmt2(summary.total_wall_time_seconds) + '</div></div>' +
                perTestSummaryHtml +
                testsPassedHtml +
            '</div>';
        resultsContainer.appendChild(summaryDiv);

        // Correctness results
        var correctnessResults = data.correctness_results || [];
        for (var i = 0; i < correctnessResults.length; i++) {
            var cr = correctnessResults[i];
            // Always use PASS semantics: score represents percentage PASSED
            var pct = Math.round((cr.score || 0) * 100);
            var status = 'PASS';
            var statusColor = cr.passed ? '#22c55e' : '#ef4444';

            var corrDiv = document.createElement("div");
            corrDiv.className = "results-group";

            var detailHtml = "";
            if (cr.test_type === "markdown") {
                // Use explicit null checks for correctness fields — do NOT map null to 0.
                // Null means the validator could not produce a count (e.g., no final answer).
                function fmtNullable(val) {
                    return (val !== null && val !== undefined) ? String(val) : '\u2014';
                }
                detailHtml =
                    '<div class="aggregate-item"><div class="label">Initial Errors</div><div class="value">' + fmtNullable(cr.initial_errors) + '</div></div>' +
                    '<div class="aggregate-item"><div class="label">Final Errors</div><div class="value">' + fmtNullable(cr.final_errors) + '</div></div>' +
                    '<div class="aggregate-item"><div class="label">Errors Fixed</div><div class="value">' + fmtNullable(cr.errors_fixed) + '</div></div>';

                // Show failure reason as a status row when present (e.g., "no_final_answer")
                if (cr.failure_reason) {
                    var statusLabel = cr.failure_reason.replace(/_/g, ' ').toUpperCase();
                    detailHtml +=
                        '<div class="aggregate-item">' +
                            '<div class="label">Status</div>' +
                            '<div class="value" style="color:#f59e0b;font-weight:bold;">' + statusLabel + '</div>' +
                        '</div>';
                }
            } else if (cr.test_type === "python") {
                detailHtml =
                    '<div class="aggregate-item"><div class="label">Passed</div><div class="value">' + (cr.passed_tests || 0) + '</div></div>' +
                    '<div class="aggregate-item"><div class="label">Total</div><div class="value">' + (cr.total_tests || 0) + '</div></div>' +
                    '<div class="aggregate-item"><div class="label">Failed</div><div class="value">' + (cr.failed_tests || 0) + '</div></div>';
            } else if (cr.test_type === "java") {
                detailHtml =
                    '<div class="aggregate-item"><div class="label">Tests Passed</div><div class="value">' + (cr.passed_tests || 0) + ' / ' + (cr.total_tests || 0) + '</div></div>' +
                    '<div class="aggregate-item"><div class="label">Compile</div><div class="value">' + (cr.compile_success ? 'PASS' : 'FAIL') + '</div></div>';
            }

            corrDiv.innerHTML =
                '<h4>Correctness: <code>' + escapeHtml(cr.test_label || cr.test_type) + '</code></h4>' +
                '<div class="aggregate">' +
                    '<div class="aggregate-item">' +
                        '<div class="label">Score</div>' +
                        '<div class="value" style="color:' + statusColor + '; font-weight:bold;">' + pct + '% ' + status + '</div>' +
                    '</div>' +
                    '<div class="aggregate-item"><div class="label">tok/s</div><div class="value">' + fmt2(cr.tokens_per_second) + '</div></div>' +
                    '<div class="aggregate-item"><div class="label">TTFT</div><div class="value">' + formatTtft(cr.ttft_seconds) + '</div></div>' +
                    '<div class="aggregate-item"><div class="label">Wall (s)</div><div class="value">' + fmt2(cr.wall_time_seconds) + '</div></div>' +
                    detailHtml +
                '</div>';
            resultsContainer.appendChild(corrDiv);
        }

        // Per-test details
        var speedResults = data.speed_results || [];
        for (var i = 0; i < speedResults.length; i++) {
            var sr = speedResults[i];
            var testDiv = document.createElement("div");
            testDiv.className = "results-group";
            testDiv.innerHTML =
                '<h4>Speed Test: <code>' + escapeHtml(sr.prompt_label || sr.test_name) + '</code></h4>' +
                '<div class="aggregate">' +
                    '<div class="aggregate-item"><div class="label">Avg tok/s</div><div class="value">' + fmt2(sr.aggregate.avg_tokens_per_second) + '</div></div>' +
                    '<div class="aggregate-item"><div class="label">Min tok/s</div><div class="value">' + fmt2(sr.aggregate.min_tokens_per_second) + '</div></div>' +
                    '<div class="aggregate-item"><div class="label">Max tok/s</div><div class="value">' + fmt2(sr.aggregate.max_tokens_per_second) + '</div></div>' +
                    '<div class="aggregate-item"><div class="label">Avg TTFT</div><div class="value">' + formatTtft(sr.aggregate.avg_ttft_seconds) + '</div></div>' +
                '</div>';
            resultsContainer.appendChild(testDiv);
        }

    } catch (e) {
        showStatus("Evaluation failed: " + e.message, "error");
    } finally {
        disableRun(false);
    }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadConfig().then(function () {
    // Auto-load models after config is loaded
    loadModels();
    // Initialize prompt presets
    initPromptPresets();
    // Load tasks
    loadTasks();
});
