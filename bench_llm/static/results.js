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

// Active view — default to "raw" (Raw Speed)
var activeView = "raw"; // "raw", "markdown", "python", "java", "unsolvable"

// ---------------------------------------------------------------------------
// Load results from backend
// ---------------------------------------------------------------------------
async function loadResults() {
    try {
        var resp = await fetch("/api/results");
        if (!resp.ok) return;
        var data = await resp.json();
        allRuns = data.results || [];
        applyFilters();
    } catch (e) {
        allRuns = [];
        applyFilters();
    }
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------
function applyFilters() {
    var modelFilter = (document.getElementById("filter-model").value || "").toLowerCase();
    var hardwareFilter = (document.getElementById("filter-hardware").value || "").toLowerCase();
    var envFilter = document.getElementById("filter-env").value || "";

    filteredRuns = allRuns.filter(function (r) {
        if (modelFilter && !(r.model_key || "").toLowerCase().includes(modelFilter) && !(r.model_display_name || "").toLowerCase().includes(modelFilter)) {
            return false;
        }
        if (hardwareFilter && !(r.hardware || "").toLowerCase().includes(hardwareFilter)) {
            return false;
        }
        if (envFilter && (r.environment || "") !== envFilter) {
            return false;
        }
        return true;
    });

    renderResults();
}

// Clear filters button
var clearFiltersBtn = document.getElementById("clear-filters");
if (clearFiltersBtn) {
    clearFiltersBtn.addEventListener("click", function () {
        document.getElementById("filter-model").value = "";
        document.getElementById("filter-hardware").value = "";
        document.getElementById("filter-env").value = "";
        applyFilters();
    });
}

// Debounced filter inputs
var filterInputs = ["filter-model", "filter-hardware"];
for (var fi = 0; fi < filterInputs.length; fi++) {
    (function (id) {
        var el = document.getElementById(id);
        if (!el) return;
        var timer;
        el.addEventListener("input", function () {
            clearTimeout(timer);
            timer = setTimeout(function () { applyFilters(); }, 300);
        });
    })(filterInputs[fi]);
}

var envFilterEl = document.getElementById("filter-env");
if (envFilterEl) {
    envFilterEl.addEventListener("change", function () { applyFilters(); });
}

// ---------------------------------------------------------------------------
// Render Results (Raw Speed / Past Runs)
// ---------------------------------------------------------------------------
function renderResults() {
    resultsPanel.classList.remove("hidden");
    chartsPanel.classList.remove("hidden");
    emptyState.classList.add("hidden");
    resultsContainer.innerHTML = "";

    if (filteredRuns.length === 0) {
        resultsPanel.classList.add("hidden");
        chartsPanel.classList.add("hidden");
        emptyState.classList.remove("hidden");
        return;
    }

    // Group by run_id
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

    // Sort by timestamp descending
    order.sort(function (a, b) {
        var ta = groups[a].timestamp || "";
        var tb = groups[b].timestamp || "";
        return tb.localeCompare(ta);
    });

    for (var gi = 0; gi < order.length; gi++) {
        var rid = order[gi];
        var group = groups[rid];
        var card = document.createElement("div");
        card.className = "benchmark-run-card";
        card.setAttribute("data-run-id", rid);

        // Header
        var header = document.createElement("div");
        header.className = "run-header";

        var modelSpan = document.createElement("span");
        modelSpan.className = "badge badge-model";
        modelSpan.textContent = group.model;
        modelSpan.title = "Model";
        header.appendChild(modelSpan);

        var tsSpan = document.createElement("span");
        tsSpan.className = "run-timestamp";
        tsSpan.textContent = formatTimestamp(group.timestamp);
        header.appendChild(tsSpan);

        card.appendChild(header);

        // Runs in this group
        var runs = group.runs;
        for (var ri = 0; ri < runs.length; ri++) {
            var r = runs[ri];
            var row = document.createElement("div");
            row.className = "run-row" + (r.cold_or_warm === "warm" ? " warm" : " cold");

            var modelLabel = document.createElement("span");
            modelLabel.className = "run-model";
            modelLabel.textContent = r.model_display_name || r.model_key || "";
            row.appendChild(modelLabel);

            var tpsSpan = document.createElement("span");
            tpsSpan.className = "run-tps";
            tpsSpan.textContent = fmt2(r.tokens_per_second) + " tok/s";
            row.appendChild(tpsSpan);

            var coldWarm = document.createElement("span");
            coldWarm.className = "badge badge-" + (r.cold_or_warm === "warm" ? "warm" : "cold");
            coldWarm.textContent = r.cold_or_warm === "warm" ? "Warm" : "Cold";
            row.appendChild(coldWarm);

            var ttftSpan = document.createElement("span");
            ttftSpan.className = "run-ttft";
            ttftSpan.textContent = "TTFT: " + formatTtft(r.ttft_seconds);
            row.appendChild(ttftSpan);

            card.appendChild(row);
        }

        // Actions
        var actions = document.createElement("div");
        actions.className = "run-actions";

        var delBtn = document.createElement("button");
        delBtn.className = "delete-btn";
        delBtn.textContent = "Delete";
        delBtn.title = "Delete this benchmark run";
        delBtn.setAttribute("data-run-id", rid);
        delBtn.setAttribute("data-model", group.model);
        delBtn.setAttribute("data-timestamp", group.timestamp);
        actions.appendChild(delBtn);

        card.appendChild(actions);
        resultsContainer.appendChild(card);
    }

    // Update results count
    if (resultsCountEl) {
        resultsCountEl.textContent = "Showing " + filteredRuns.length + " result(s) from " + order.length + " run(s)";
    }

    // Render historical comparison chart
    renderHistoryCharts();
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
// Init
// ---------------------------------------------------------------------------

loadResults();