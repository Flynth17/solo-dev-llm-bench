/** Solo Dev LLM Bench - Dashboard prompt preset management. */

// ---------------------------------------------------------------------------
// DOM references (prompt-specific)
// ---------------------------------------------------------------------------
var promptPresetSelect = document.getElementById("prompt-preset");
var savePromptBtn = document.getElementById("save-prompt-btn");
var renamePromptBtn = document.getElementById("rename-prompt-btn");
var deletePromptBtn = document.getElementById("delete-prompt-btn");
var promptInput = document.getElementById("prompt");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

// Track the currently loaded preset name (empty if custom)
var currentPresetName = "";

// Cache for prompts (used by determinePromptName)
var _cachedPrompts = [];

// ---------------------------------------------------------------------------
// Helpers
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

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------

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