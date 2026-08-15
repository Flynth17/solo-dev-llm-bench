/** Solo Dev LLM Bench - Results page filter logic (extracted). */

// Filter DOM references
var filterModelInput = document.getElementById("filter-model");
var filterBar = document.getElementById("filter-bar");
var filterHardwareInput = document.getElementById("filter-hardware");
var filterEnvSelect = document.getElementById("filter-env");
var clearFiltersBtn = document.getElementById("clear-filters");

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
// Filter event listeners
// ---------------------------------------------------------------------------

filterModelInput.addEventListener("input", applyFilters);
filterHardwareInput.addEventListener("input", applyFilters);
filterEnvSelect.addEventListener("change", applyFilters);
clearFiltersBtn.addEventListener("click", clearFilters);