/** Solo Dev LLM Bench - dashboard client-side logic. */

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
var hardwareLabelInput = document.getElementById("hardware-label");
var lmStudioUrlInput = document.getElementById("lm-studio-url");
var modelSelect = document.getElementById("model-select");
var refreshModelsBtn = document.getElementById("refresh-models");
var statusEl = document.getElementById("status");

// --- Model lifecycle (Act 18) -------------------------------------------------
var modelStateLine = document.getElementById("model-state-line");
var loadUnloadBtn = document.getElementById("load-unload-btn");

// In-memory snapshot of the last /api/models fetch keyed by model key. Only fields the
// launcher needs for a concise, honest line are stored; nothing is fabricated.
var _modelsByKey = {};
// Remember selection across refreshes so reloads don't drop the user's choice.
var _prevSelection = "";

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

// ---------------------------------------------------------------------------
// Runtime status bar (display-only; state is driven by real model-fetch results)
// ---------------------------------------------------------------------------
function setRuntimeState(state, label) {
    var dot = document.getElementById("runtime-dot");
    var txt = document.getElementById("runtime-state");
    if (dot) {
        dot.className = "dapp-dot dapp-dot-" + state;
    }
    if (txt && label !== undefined) {
        txt.textContent = label;
    }
}

function refreshRuntimeHost() {
    var hostEl = document.getElementById("runtime-host");
    if (!hostEl || !lmStudioUrlInput) { return; }
    var raw = lmStudioUrlInput.value.replace(/\/$/, "").replace(/^https?:\/\//, "");
    hostEl.textContent = raw || "localhost:1234";
}

if (modelSelect) {
    modelSelect.addEventListener("change", function () {
        _prevSelection = this.value;
        var modelLabel = document.getElementById("runtime-model");
        if (!modelLabel) { return; }
        if (this.value) {
            var opt = this[this.selectedIndex];
            modelLabel.textContent = opt ? opt.textContent.split(" (")[0] : "\u2014 no model selected \u2014";
        } else {
            modelLabel.textContent = "\u2014 no model selected \u2014";
        }
        syncModelControls();
    });
}

if (lmStudioUrlInput) {
    lmStudioUrlInput.addEventListener("input", refreshRuntimeHost);
}

/** Format an integer context size with thousands separators; — when absent/invalid. */
function fmtNum(value) {
    if (value === null || value === undefined || value === "") return "\u2014";
    var n = Number(value);
    if (!isFinite(n)) return "\u2014";
    var s = Math.round(n).toString();
    var neg = s.charAt(0) === "-";
    if (neg) s = s.slice(1);
    return (neg ? "-" : "") + s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** Render a quantization value to a short label (mirrors the model-option parsing). */
function _quantName(raw) {
    if (!raw) return "";
    if (typeof raw === "string") return raw;
    if (typeof raw === "object" && raw !== null) {
        for (var k of ["name", "display_name", "quant"]) {
            if (typeof raw[k] === "string" && raw[k]) return raw[k];
        }
    }
    return "";
}

// Gate Speed + Workflow on a selected AND loaded model. Context stays Planned/off.
function setSuiteGating(enabled) {
    if (runSpeedBtn) runSpeedBtn.disabled = !enabled;
    if (runWorkflowBtn) runWorkflowBtn.disabled = !enabled;
}

// Re-render the compact state line + Load/Unload button and gate the suites in one pass.
function syncModelControls() {
    var sel = modelSelect ? modelSelect.value : "";
    if (!sel) {
        if (modelStateLine) {
            modelStateLine.textContent = "Select a model to inspect its runtime state.";
            try { modelStateLine.removeAttribute("data-status"); } catch (e) {}
        }
        if (loadUnloadBtn) { loadUnloadBtn.disabled = true; loadUnloadBtn.textContent = "Load Model"; }
        setSuiteGating(false);
        return;
    }
    var m = _modelsByKey[sel];
    var name = "";
    if (m && m.name) {
        name = m.name;
    } else if (modelSelect) {
        var opt = modelSelect[modelSelect.selectedIndex];
        name = opt ? opt.textContent.split(" (")[0] : "";
    }

    var dotCls = "dapp-dot-bad";   // unknown / unavailable
    var lineText = "";
    if (!m) {
        dotCls = "dapp-dot-warn";
        lineText = "\u2014 " + name + " \u2014 (state unavailable)";
    } else if (m.loaded) {
        dotCls = "dapp-dot-ok";
        var ctx = m.context_length != null ? m.context_length : m.max_context_length;
        lineText = "\u25CF Loaded \u00B7 " + (_quantName(m.quantization) || "\u2014") + " \u00B7 Context " + fmtNum(ctx);
    } else {
        dotCls = "dapp-dot-idle";
        lineText = "\u25CB Not loaded \u00B7 " + (_quantName(m.quantization) || "\u2014") + " \u00B7 Max context " + fmtNum(m.max_context_length);
    }
    if (modelStateLine) {
        modelStateLine.innerHTML = '<span class="' + dotCls + '"></span><span>' + lineText + "</span>";
        var statusAttr = m ? (m.loaded ? "loaded" : "unloaded") : "unknown";
        try { modelStateLine.setAttribute("data-status", statusAttr); } catch (e) {}
    }
    if (loadUnloadBtn) {
        loadUnloadBtn.disabled = false;
        loadUnloadBtn.textContent = m && m.loaded ? "Unload Model" : "Load Model";
    }
    setSuiteGating(!!(m && m.loaded));
}

var _lifecycleMutating = false;
function _setLifecycleButton(disabled, text) {
    if (loadUnloadBtn) { loadUnloadBtn.disabled = disabled; loadUnloadBtn.textContent = text; }
}

// Backend is authoritative for every safety rule (benchmark guard, other-loaded model,
// ambiguity). This only performs the request and then refreshes authoritative state.
async function doLifecycleAction(kind) {
    if (_lifecycleMutating) return;
    var sel = modelSelect ? modelSelect.value : "";
    if (!sel) { showStatus("Please select a model first.", "error"); return; }
    _lifecycleMutating = true;
    var verb = kind === "load" ? "Loading" : "Unloading";
    _setLifecycleButton(true, verb + "\u2026");
    clearStatus();
    try {
        var resp = await fetch("/api/models/" + kind, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model: sel, lm_studio_url: lmStudioUrlInput ? lmStudioUrlInput.value : "" })
        });
        var data = await resp.json().catch(function () { return null; });
        if (!resp.ok) {
            showStatus((data && data.detail) || ("Model " + kind + " failed."), "error");
        } else {
            // Refresh authoritative state from /api/models (also re-gates the suites).
            await loadModels();
        }
    } catch (e) {
        showStatus("Network error while " + verb.toLowerCase() + "ing the model.", "error");
    } finally {
        _lifecycleMutating = false;
        syncModelControls();
    }
}

if (loadUnloadBtn) {
    loadUnloadBtn.addEventListener("click", function () {
        var sel = modelSelect ? modelSelect.value : "";
        if (!sel) { showStatus("Please select a model first.", "error"); return; }
        doLifecycleAction(loadUnloadBtn.textContent.indexOf("Unload") === 0 ? "unload" : "load");
    });
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

// NOTE: the execution-environment / connection-type controls were removed from the
// standard launcher (Act 14). Runtime identity now defaults to Local; advanced custom
// benchmark still passes these values when present.

// ---------------------------------------------------------------------------
// Load initial config
// ---------------------------------------------------------------------------

async function loadConfig() {
    try {
        var resp = await fetch("/api/config");
        var config = await resp.json();
        if (lmStudioUrlInput) lmStudioUrlInput.value = config.lm_studio_url || "http://localhost:1234";
        // New fields
        if (config.hardware_label && hardwareLabelInput) {
            hardwareLabelInput.value = config.hardware_label;
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

        // Preserve the user's selection across refreshes when still valid.
        var keepSel = _prevSelection || (modelSelect ? modelSelect.value : "");

        modelSelect.innerHTML = '<option value="">\u2014 Select a model \u2014</option>';
        for (var i = 0; i < models.length; i++) {
            var m = models[i];
            // Snapshot the fields the lifecycle panel needs (key, display name, loaded,
            // configured max context, raw quantization). No fabricated values.
            _modelsByKey[m.key] = {
                key: m.key,
                name: (m.name || "").split(" (")[0],
                loaded: !!m.loaded,
                max_context_length: (typeof m.max_context_length === "number") ? m.max_context_length : null,
                quantization: m.quantization || "",
            };
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

        // Restore the user's selection if it survived the refresh; otherwise clear it.
        if (keepSel) {
            for (var j = 0; j < modelSelect.options.length; j++) {
                if (modelSelect.options[j].value === keepSel) { modelSelect.value = keepSel; break; }
            }
            var stillThere = Array.prototype.some.call(modelSelect.options, function (o) { return o.value === keepSel; });
            if (!stillThere) _prevSelection = "";
        }

        // Render the compact model-state line + Load/Unload button and gate the suites.
        syncModelControls();

        if (models.length === 0) {
            setRuntimeState("warn", "No models");
            showStatus("No LLM models found. Load a model in LM Studio first.", "info");
        } else {
            setRuntimeState("ok", "Ready");
            clearStatus();
        }
    } catch (e) {
        setRuntimeState("bad", "Unavailable");
        showStatus("Failed to fetch models: " + e.message, "error");
    }
}

refreshModelsBtn.addEventListener("click", loadModels);

// ---------------------------------------------------------------------------
// Load & display results
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Three-suite launcher actions (standard contracts)
// ---------------------------------------------------------------------------
// Speed / Workflow / Context are predefined, comparable contracts. Their execution
// plumbing lives in dedicated backend Acts. These handlers never call a legacy or
// unsafe path -- they only surface the current execution status if a card button is
// ever enabled before its backend route exists.

var runSpeedBtn = document.getElementById("run-speed");
var runWorkflowBtn = document.getElementById("run-workflow");
var runContextBtn = document.getElementById("run-context");

function suiteStatusLabel(label, message) {
    return function () {
        showStatus(message, "info");
    };
}
// ---------------------------------------------------------------------------
// Standard Speed Suite launcher (Act 17).
//
// Launching is execution plumbing only: the backend starts the LOCKED standard
// speed runner as a separate OS process (python -m src v2-speed -> run_speed_suite)
// and returns immediately with a run id. This handler never runs a benchmark in
// process, never calls the legacy executor, and exposes NO per-point / iteration /
// output overrides -- the fixed 8K · 16K · 32K contract is decided by the backend.
// ---------------------------------------------------------------------------
var speedPollTimer = null;
var speedRunId = null;

function clearSpeedPoll() {
    if (speedPollTimer) {
        clearInterval(speedPollTimer);
        speedPollTimer = null;
    }
    try { window.sessionStorage.removeItem("speed_run_id"); } catch (e) {}
}

// Restore the control to a plain "Run Speed" button (also converts a completed
// "View Result" link back into a fresh, enabled button).
function resetSpeedButton() {
    clearSpeedPoll();
    var ctrl = runSpeedBtn;
    if (!ctrl) { return; }
    if (ctrl.tagName !== "BUTTON") {
        var b = document.createElement("button");
        b.id = "run-speed";
        b.className = "suite-btn suite-btn-run";
        b.type = "button";
        b.textContent = "Run Speed";
        b.title = "Standard Speed suite: 8K · 16K · 32K TTFT, prefill and generation throughput. Launched in a backend process.";
        ctrl.replaceWith(b);
        runSpeedBtn = b;
    } else {
        ctrl.disabled = false;
        ctrl.textContent = "Run Speed";
    }
}

function makeSpeedResultLink(href, runId) {
    var ctrl = runSpeedBtn;
    if (!ctrl) { return; }
    var a = document.createElement("a");
    a.className = "suite-btn suite-btn-run";
    a.href = href;
    a.textContent = "View Result";
    a.title = "Open the Speed result for " + runId;
    ctrl.replaceWith(a);
}

// Poll the status endpoint (~3s). Transient errors are ignored so we keep waiting.
function pollSpeedStatus(runId) {
    fetch("/api/v2/speed/runs/" + encodeURIComponent(runId) + "/status")
        .then(function (r) { return r.json(); })
        .then(function (state) {
            if (!state || !state.status) { return; }
            if (state.status === "completed") {
                clearSpeedPoll();
                showStatus("Speed complete", "success");
                makeSpeedResultLink(state.result_url || ("/speed/results/" + runId), runId);
            } else if (state.status === "failed") {
                resetSpeedButton();
                showStatus((state.error && state.error.length) ? state.error : "Speed failed", "error");
            } else if (state.status === "running") {
                // keep polling
            }
        })
        .catch(function () { /* transient; keep polling */ });
}

function startSpeedPoll(runId) {
    clearSpeedPoll();
    speedRunId = runId;
    try { window.sessionStorage.setItem("speed_run_id", runId); } catch (e) {}
    if (runSpeedBtn && runSpeedBtn.tagName === "BUTTON") {
        runSpeedBtn.disabled = true;
        runSpeedBtn.textContent = "Running…";
    }
    showStatus("Speed running • " + runId, "info");
    speedPollTimer = setInterval(function () {
        pollSpeedStatus(runId);
    }, 3000);
}

if (runSpeedBtn) {
    runSpeedBtn.addEventListener("click", function () {
        var model = modelSelect ? modelSelect.value : "";
        if (!model) {
            showStatus("Please select a model before launching the Speed suite.", "error");
            return;
        }
        // Disable during the launch request (Starting…).
        runSpeedBtn.disabled = true;
        runSpeedBtn.textContent = "Starting…";
        showStatus("Launching speed…", "info");
        fetch("/api/v2/speed/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                model: model,
                lm_studio_url: lmStudioUrlInput ? lmStudioUrlInput.value : ""
            })
        })
        .then(function (r) { return r.json().catch(function () { return null; }); })
        .then(function (data) {
            if (data && data.speed_run_id) {
                startSpeedPoll(data.speed_run_id);
            } else if (data && data.detail && /already in progress/i.test(data.detail)) {
                resetSpeedButton();
                showStatus(data.detail, "error");
            } else if (data && data.detail) {
                resetSpeedButton();
                showStatus(data.detail, "error");
            } else {
                resetSpeedButton();
                showStatus("Unable to start the Speed suite. Please try again.", "error");
            }
        })
        .catch(function () {
            resetSpeedButton();
            showStatus("Network error while launching the Speed suite.", "error");
        });
    });
}

// Resume a speed run in progress after a page reload (simple, bounded).
(function resumeSpeed() {
    var saved = null;
    try { saved = window.sessionStorage.getItem("speed_run_id"); } catch (e) {}
    if (saved && runSpeedBtn) { startSpeedPoll(saved); }
})();
// ---------------------------------------------------------------------------
// Workflow suite launcher (Act 16).
//
// Launching is execution plumbing only: the backend starts the LOCKED workflow
// runner as a separate OS process (python -m src v2-run -> run_v2_run) and returns
// immediately with a run id. This handler never runs a benchmark in-process, never
// calls the legacy executor, and exposes no per-suite / temperature / output /
// reasoning overrides -- those are decided by the locked Workflow contract.
// ---------------------------------------------------------------------------
var workflowPollTimer = null;
var workflowRunId = null;

function clearWorkflowPoll() {
    if (workflowPollTimer) {
        clearInterval(workflowPollTimer);
        workflowPollTimer = null;
    }
    try { window.sessionStorage.removeItem("wf_run_id"); } catch (e) {}
}

// Restore the control to a plain "Run Workflow" button (also converts a completed
// "View Result" link back into a fresh, enabled button).
function resetWorkflowButton() {
    clearWorkflowPoll();
    var ctrl = runWorkflowBtn;
    if (!ctrl) { return; }
    if (ctrl.tagName !== "BUTTON") {
        var b = document.createElement("button");
        b.id = "run-workflow";
        b.className = "suite-btn suite-btn-run";
        b.type = "button";
        b.textContent = "Run Workflow";
        b.title = "Locked Workflow suite: 166 deterministic checks. Launched in a backend process.";
        ctrl.replaceWith(b);
        runWorkflowBtn = b;
    } else {
        ctrl.disabled = false;
        ctrl.textContent = "Run Workflow";
    }
}

function makeWorkflowResultLink(href, runId) {
    var ctrl = runWorkflowBtn;
    if (!ctrl) { return; }
    var a = document.createElement("a");
    a.className = "suite-btn suite-btn-run";
    a.href = href;
    a.textContent = "View Result";
    a.title = "Open the Workflow result for " + runId;
    ctrl.replaceWith(a);
}

// Poll the status endpoint (~3s). Transient errors are ignored so we keep waiting.
function pollWorkflowStatus(runId) {
    fetch("/api/v2/workflow/runs/" + encodeURIComponent(runId) + "/status")
        .then(function (r) { return r.json(); })
        .then(function (state) {
            if (!state || !state.status) { return; }
            if (state.status === "completed") {
                clearWorkflowPoll();
                showStatus("Workflow complete", "success");
                makeWorkflowResultLink(state.result_url || ("/v2/results/" + runId), runId);
            } else if (state.status === "failed") {
                resetWorkflowButton();
                showStatus((state.error && state.error.length) ? state.error : "Workflow failed", "error");
            } else if (state.status === "running") {
                // keep polling
            }
        })
        .catch(function () { /* transient; keep polling */ });
}

function startWorkflowPoll(runId) {
    clearWorkflowPoll();
    workflowRunId = runId;
    try { window.sessionStorage.setItem("wf_run_id", runId); } catch (e) {}
    if (runWorkflowBtn && runWorkflowBtn.tagName === "BUTTON") {
        runWorkflowBtn.disabled = true;
        runWorkflowBtn.textContent = "Running…";
    }
    showStatus("Workflow running • " + runId, "info");
    workflowPollTimer = setInterval(function () {
        pollWorkflowStatus(runId);
    }, 3000);
}

if (runWorkflowBtn) {
    runWorkflowBtn.addEventListener("click", function () {
        var model = modelSelect ? modelSelect.value : "";
        if (!model) {
            showStatus("Please select a model before launching the Workflow suite.", "error");
            return;
        }
        // Disable during the launch request (Starting…).
        runWorkflowBtn.disabled = true;
        runWorkflowBtn.textContent = "Starting…";
        showStatus("Launching workflow…", "info");
        fetch("/api/v2/workflow/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                model: model,
                lm_studio_url: lmStudioUrlInput ? lmStudioUrlInput.value : ""
            })
        })
        .then(function (r) { return r.json().catch(function () { return null; }); })
        .then(function (data) {
            if (data && data.run_id) {
                startWorkflowPoll(data.run_id);
            } else if (data && data.detail && /already in progress/i.test(data.detail)) {
                resetWorkflowButton();
                showStatus(data.detail, "error");
            } else if (data && data.detail) {
                resetWorkflowButton();
                showStatus(data.detail, "error");
            } else {
                resetWorkflowButton();
                showStatus("Unable to start the Workflow suite. Please try again.", "error");
            }
        })
        .catch(function () {
            resetWorkflowButton();
            showStatus("Network error while launching the Workflow suite.", "error");
        });
    });
}

// Resume a workflow run in progress after a page reload (simple, bounded).
(function resumeWorkflow() {
    var saved = null;
    try { saved = window.sessionStorage.getItem("wf_run_id"); } catch (e) {}
    if (saved && runWorkflowBtn) { startWorkflowPoll(saved); }
})();
if (runContextBtn) {
    runContextBtn.addEventListener("click", suiteStatusLabel(
        "Context",
        "The Context suite execution is not available yet."));
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadConfig().then(function () {
    // Reflect the configured LM Studio endpoint in the runtime status bar
    if (lmStudioUrlInput) refreshRuntimeHost();
    setRuntimeState("idle", "Not checked");
    // Auto-load models after config is loaded
    loadModels();
});