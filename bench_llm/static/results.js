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
// Render Results (Raw Speed / Past Runs)
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