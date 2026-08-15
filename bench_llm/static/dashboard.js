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
maxTokensInput.value = config.max_tokens || 1024;
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
// Task Manager
// ---------------------------------------------------------------------------

var taskTypeSelect = document.getElementById("task-type-select");
var taskNameInput = document.getElementById("task-name-input");
var createTaskBtn = document.getElementById("create-task-btn");
var taskListContainer = document.getElementById("task-list");

/** Cached task list for incremental updates. */
var _cachedTasks = [];

/** Load all tasks from the API and render them. */
async function loadTasks() {
    try {
        var resp = await fetch("/api/tasks");
        if (!resp.ok) return;
        var data = await resp.json();
        _cachedTasks = data.tasks || [];
        renderTasks();
    } catch (_) {
        taskListContainer.innerHTML = '<p style="color:#dc2626;">Failed to load tasks.</p>';
    }
}

/** Render the cached task list into the DOM. */
function renderTasks() {
    taskListContainer.innerHTML = "";
    if (_cachedTasks.length === 0) {
        taskListContainer.innerHTML = '<p style="color:#64748b;font-style:italic;">No tasks yet. Create one above.</p>';
        return;
    }
    for (var i = 0; i < _cachedTasks.length; i++) {
        var t = _cachedTasks[i];
        var div = document.createElement("div");
        div.className = "task-item" + (t.status ? " " + t.status : "");
        div.setAttribute("data-task-id", t.task_id);

        var nameSpan = document.createElement("span");
        nameSpan.className = "task-name";
        nameSpan.textContent = t.name || "Unnamed";
        div.appendChild(nameSpan);

        var badge = document.createElement("span");
        badge.className = "task-type-badge " + (t.task_type || "");
        badge.textContent = (t.task_type || "").toUpperCase();
        div.appendChild(badge);

        var statusSpan = document.createElement("span");
        statusSpan.className = "task-status " + (t.status || "");
        statusSpan.textContent = (t.status || "pending").toUpperCase();
        div.appendChild(statusSpan);

        var timeSpan = document.createElement("span");
        timeSpan.className = "task-timestamp";
        timeSpan.textContent = t.created_at ? formatTimestamp(t.created_at) : "";
        div.appendChild(timeSpan);

        var actionsDiv = document.createElement("div");
        actionsDiv.className = "task-actions";

        // Run button (only if not running/completed)
        if (t.status !== "running") {
            var runBtn = document.createElement("button");
            runBtn.className = "run-btn";
            runBtn.title = "Run benchmark";
            runBtn.textContent = "\u23F5"; // hourglass
            runBtn.addEventListener("click", (function(taskId) {
                return function() { runTask(taskId); };
            })(t.task_id));
            actionsDiv.appendChild(runBtn);
        }

        // Delete button
        var delBtn = document.createElement("button");
        delBtn.className = "delete-btn-task";
        delBtn.title = "Delete task";
        delBtn.textContent = "\u2716"; // ×
        delBtn.addEventListener("click", (function(taskId) {
            return function() { deleteTask(taskId); };
        })(t.task_id));
        actionsDiv.appendChild(delBtn);

        div.appendChild(actionsDiv);

        // Result display
        if (t.result) {
            var resultDiv = document.createElement("div");
            resultDiv.className = "task-result";
            resultDiv.innerHTML = renderTaskResult(t);
            div.appendChild(resultDiv);
        }

        taskListContainer.appendChild(div);
    }
}

/** Render a task result summary. */
function renderTaskResult(task) {
    var result = task.result || {};
    var aggregate = result.aggregate || {};
    var taskType = task.task_type || "";
    var scoreKey = taskType + "_score";
    var score = aggregate["avg_" + scoreKey] || aggregate[scoreKey] || null;
    var meetsMin = result["meets_minimum"] || aggregate[taskType + "_meets_minimum"] || false;

    var html = '<div style="display:flex;gap:1rem;flex-wrap:wrap;align-items:center;">';
    if (score !== null) {
        var cls = meetsMin ? "pass" : "fail";
        html += '<span class="task-result-score ' + cls + '">Score: ' + score + '</span>';
    }
    if (aggregate["avg_tokens_per_second"]) {
        html += '<span>TPS: <strong>' + aggregate["avg_tokens_per_second"] + '</strong></span>';
    }
    html += '</div>';
    return html;
}

/** Create a new task. */
async function createTask() {
    var taskType = taskTypeSelect.value;
    var taskName = taskNameInput.value.trim() || (taskType + " Task");

    var prompt = "";
    // Load default prompt for this type
    var defaultPrompts = {
        "markdown": "Write a short technical document about AI code assistants that includes: headings, an unordered list, an ordered list, a fenced code block with Python code, a table, a horizontal rule, and a blockquote. Keep it under 500 tokens.",
        "python": "Write a Python function called process_data that: 1. Takes a list[int] and a str as parameters with type hints 2. Has a docstring explaining its purpose 3. Filters the list to keep only even numbers 4. Returns a dict with the original length and filtered list 5. Include import statements. Keep it under 300 tokens.",
        "java": "Write a Java class called Person that: 1. Has a class declaration with Javadoc comment 2. Has private fields: name (String) and age (int) 3. Has a constructor with parameters 4. Has getter methods: getName() returns String, getAge() returns int 5. Has a setter method: setName(String name) 6. Includes import statements. Keep it under 300 tokens."
    };
    prompt = defaultPrompts[taskType] || "";

    try {
        var resp = await fetch("/api/tasks", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name: taskName,
                task_type: taskType,
                prompt: prompt
            })
        });

        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }

        var data = await resp.json();
        showStatus("Task created: " + taskName, "info");

        // Reload tasks
        loadTasks();

        // Clear name input
        taskNameInput.value = "";
    } catch (e) {
        showStatus("Failed to create task: " + e.message, "error");
    }
}

/** Run a benchmark for a task. */
async function runTask(taskId) {
    var task = null;
    for (var i = 0; i < _cachedTasks.length; i++) {
        if (_cachedTasks[i].task_id === taskId) {
            task = _cachedTasks[i];
            break;
        }
    }
    if (!task) {
        showStatus("Task not found.", "error");
        return;
    }

    var model = modelSelect.value.trim();
    if (!model) {
        showStatus("Please select a model first.", "error");
        return;
    }

    var config = {
        lm_studio_url: lmStudioUrlInput.value.replace(/\/$/, ""),
        model: model,
        max_tokens: parseInt(maxTokensInput.value, 10) || 500,
        temperature: parseFloat(temperatureInput.value) || 0,
        iterations: parseInt(iterationsInput.value, 10) || 3,
        hardware_label: hardwareLabelInput.value.trim(),
        execution_environment: executionEnvSelect.value,
        connection_type: connectionTypeSelect.value,
    };

    // Optimistically update UI
    for (var j = 0; j < _cachedTasks.length; j++) {
        if (_cachedTasks[j].task_id === taskId) {
            _cachedTasks[j].status = "running";
            break;
        }
    }
    renderTasks();

    try {
        var resp = await fetch("/api/tasks/" + encodeURIComponent(taskId) + "/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(config)
        });

        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }

        var data = await resp.json();
        showStatus("Task completed: " + task.name, "info");

        // Reload tasks to get result
        loadTasks();
    } catch (e) {
        showStatus("Task failed: " + e.message, "error");
        // Reload tasks to get updated status
        loadTasks();
    }
}

/** Delete a task. */
async function deleteTask(taskId) {
    if (!confirm("Delete this task?")) {
        return;
    }

    try {
        var resp = await fetch("/api/tasks/" + encodeURIComponent(taskId), {
            method: "DELETE"
        });

        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }

        showStatus("Task deleted.", "info");
        loadTasks();
    } catch (e) {
        showStatus("Failed to delete task: " + e.message, "error");
    }
}

createTaskBtn.addEventListener("click", createTask);

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
