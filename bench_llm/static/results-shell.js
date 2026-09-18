/** Solo Dev LLM Bench — Unified Results shell (RM-26-AA-0016).
 *
 * Owns the shared Results shell: tab navigation across benchmark areas and the
 * Overall / Workflow views. Both consume the authoritative, read-only ranking
 * read model at `/api/ranking` (build_ranking) -- this layer formats, sorts and
 * drills down only; it never recomputes scores or invents a composite. N/A stays
 * N/A: missing values render as an em-dash ("\u2014"), never zero.
 *
 * The Speed view is owned by results.js (existing point-based history). Context
 * and Intelligence are honest placeholders for later Acts (0018 / research).
 */

(function () {
    "use strict";

    // -----------------------------------------------------------------------
    // Tab navigation (shared Results nav)
    // -----------------------------------------------------------------------
    var tabs = Array.prototype.slice.call(
        document.querySelectorAll(".rs-tab[role='tab']")
    );
    var views = {};
    tabs.forEach(function (tab) {
        var id = tab.getAttribute("aria-controls");
        if (id) { views[id] = document.getElementById(id); }
    });

    function activateView(viewId) {
        // Toggle tab selected state.
        tabs.forEach(function (tab) {
            var on = tab.getAttribute("aria-controls") === viewId;
            tab.setAttribute("aria-selected", on ? "true" : "false");
            if (on) { tab.classList.add("rs-active"); } else { tab.classList.remove("rs-active"); }
        });
        // Toggle view visibility.
        Object.keys(views).forEach(function (key) {
            var el = views[key];
            if (!el) { return; }
            if (key === viewId) { el.classList.add("rs-active"); }
            else { el.classList.remove("rs-active"); }
        });
    }

    tabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
            var viewId = tab.getAttribute("aria-controls");
            if (viewId) {
                activateView(viewId);
                // Lazily load a view's data the first time it is opened.
                ensureLoaded(viewId);
            }
        });
    });

    // Which views need on-demand data loading, keyed by view id.
    var loaders = {
        "view-overall": loadOverall,
        "view-workflow": loadWorkflow
    };
    var loaded = {};

    function ensureLoaded(viewId) {
        if (loaded[viewId]) { return; }
        var loader = loaders[viewId];
        if (loader && !loaded[viewId]) {
            loaded[viewId] = true;
            loader();
        }
    }

    // -----------------------------------------------------------------------
    // Small rendering helpers (reuse shared formatting from results-utils.js).
    // -----------------------------------------------------------------------
    function esc(text) {
        return typeof escapeHtml === "function" ? escapeHtml(String(text)) : String(text);
    }

    // Missing / NaN -> em-dash, never zero. N/A stays distinct from 0.
    function na(value) {
        if (value === null || value === undefined || value === "") { return "\u2014"; }
        var num = Number(value);
        if (typeof num !== "number" || isNaN(num)) { return "\u2014"; }
        return num;
    }

    function fmtNum(value, digits) {
        var n = na(value);
        if (n === "\u2014") { return "<span class='rs-na'>\u2014</span>"; }
        return String(n.toFixed(digits != null ? digits : 2));
    }

    function el(tag, className, html) {
        var node = document.createElement(tag);
        if (className) { node.className = className; }
        if (html != null) { node.innerHTML = html; }
        return node;
    }

    // Dimension family -> human label.
    var DIM_LABELS = {
        "speed": "Speed",
        "agentic": "Workflow / Agentic",
        "context_degradation": "Context",
        "intelligence": "Intelligence"
    };

    // -----------------------------------------------------------------------
    // Overall view (composite status + per-dimension availability + L0 list)
    // Composite score stays explicitly unavailable -- never inferred.
    // -----------------------------------------------------------------------
    function loadOverall() {
        var container = document.getElementById("overall-container");
        if (!container) { return; }
        container.innerHTML = stateLoading();

        fetch("/api/ranking")
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
            .then(function (data) { renderOverall(container, data); })
            .catch(function () { renderOverallError(container); });
    }

    function renderOverall(container, ranking) {
        var composite = ranking.composite || {};
        var html = "";

        // Composite banner: honest "no approved composite score" state.
        html += '<div class="rs-panel">';
        html += banner("unavailable", "Composite score",
            esc(composite.reason ||
                "Overall Solo Bench composite scoring contract is not yet approved; no Overall Solo Bench score is computed or inferred."));
        html += "</div>";

        // Per-dimension availability cards (authoritative from the read model).
        var dims = ranking.all_dimensions || [];
        var dimScores = ranking.models && ranking.models.length
            ? (ranking.models[0].dimensions || {})
            : {};
        // Prefer per-model dimension data; fall back to composite lists.
        html += '<div class="rs-panel">';
        html += '<div class="rs-panel-title">Dimension availability</div>';
        html += '<div class="rs-card-grid">';
        dims.forEach(function (dim) {
            var info = dimScores[dim] || {};
            var status = info.status || "unavailable";
            var cls = status === "available" ? "rs-dim-ok" : "rs-dim-unavailable";
            var label = DIM_LABELS[dim] || dim;
            var valueText;
            if (status === "available") {
                // Component score is a nested object (speed/agentic); surface a short label.
                valueText = "<span class='rs-ok-value'>Available</span>";
            } else {
                var reason = info.reason || "not available";
                valueText = "<span class='rs-dim-status " + cls + "'>" + esc(reason) + "</span>";
            }
            html += '<div class="rs-metric-card">';
            html += '<div class="rs-metric-label">' + esc(label) + "</div>";
            html += valueText;
            html += "</div>";
        });
        html += "</div></div>";

        // L0 model list (collapsible to L1 configurations). Reusable drill-down primitive.
        html += '<div class="rs-panel">';
        html += '<div class="rs-panel-title">Models</div>';
        if (!ranking.models || ranking.models.length === 0) {
            html += stateNoResults("No benchmark results yet.");
        } else {
            html += renderL0Models(ranking.models);
        }
        html += "</div>";

        container.innerHTML = html;
    }

    // L0 model identity -> expandable to L1 configurations.
    function renderL0Models(models) {
        var out = "";
        models.forEach(function (m) {
            var arch = m.architecture || "unknown";
            var dims = m.dimensions || {};
            var dimChips = "";
            Object.keys(dims).forEach(function (dim) {
                var st = dims[dim].status || "unavailable";
                var cls = st === "available" ? "rs-badge-ok" : "rs-badge-unavailable";
                dimChips += "<span class='rs-badge " + cls + "'>" + esc(DIM_LABELS[dim] || dim) + "</span>";
            });
            var configs = m.configurations || [];
            var configRows = "";
            configs.forEach(function (c) {
                var fam = c.benchmark_family;
                var avail = c.has_eligible_component_score ? "rs-badge-ok" : "rs-badge-unavailable";
                var availText = avail === "rs-badge-ok" ? "Score available" : "No eligible run";
                configRows += '<div class="rs-config-row">';
                configRows += "<span class='rs-badge " + avail + "'>" + esc(fam) + "</span>";
                configRows += "<span class='rs-config-fp' title='Configuration fingerprint'>" +
                    esc((c.configuration_fingerprint || "\u2014").slice(0, 12)) + "</span>";
                configRows += "<span class='rs-badge " + avail + "'>" + esc(availText) + "</span>";
                configRows += "<span class='rs-config-fp'>" + c.evidence_count + " evidence item(s)</span>";
                configRows += "</div>";
            });

            out += "<details class='rs-model-group' open>";
            out += "<summary>";
            out += "<span class='rs-model-name'>" + esc(m.model_family || m.model_version || "unknown") + "</span>";
            out += '<span class="rs-model-meta">';
            out += "<span class='rs-badge rs-badge-accent'>" + esc(arch) + "</span>";
            if (m.has_approved_evidence) {
                out += "<span class='rs-badge rs-badge-ok'>Approved evidence</span>";
            }
            out += dimChips;
            out += "</span></summary>";
            out += '<div class="rs-configs">' + configRows + "</div>";
            out += "</details>";
        });
        return out;
    }

    function renderOverallError(container) {
        container.innerHTML = banner("error", "Unable to load results",
            "The ranking read model could not be loaded. Try refreshing the page.");
    }

    // -----------------------------------------------------------------------
    // Workflow / Agentic view (delivered; consumes authoritative ranking).
    // Distinct states: no workflow runs yet (family delivered) vs unavailable.
    // -----------------------------------------------------------------------
    function loadWorkflow() {
        var container = document.getElementById("workflow-container");
        if (!container) { return; }
        container.innerHTML = stateLoading();

        fetch("/api/ranking")
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
            .then(function (data) { renderWorkflow(container, data); })
            .catch(function () {
                container.innerHTML = banner("error", "Unable to load workflow results",
                    "The ranking read model could not be loaded. Try refreshing the page.");
            });
    }

    function renderWorkflow(container, ranking) {
        // Collect agentic configurations across all models (authoritative grouping).
        var rows = [];
        (ranking.models || []).forEach(function (m) {
            (m.configurations || []).forEach(function (c) {
                if (c.benchmark_family === "agentic") {
                    rows.push({ model: m, config: c });
                }
            });
        });

        if (rows.length === 0) {
            // Workflow family is delivered; zero runs yet -> no-results (not unavailable).
            container.innerHTML = stateNoResults(
                "No workflow results yet. Run a Workflow suite from the benchmark page to see its deterministic correctness result here.");
            return;
        }

        var html = '<div class="rs-panel">';
        html += '<div class="rs-panel-title">Workflow / Agentic results</div>';
        html += "<p class='rs-placeholder-note' style='margin:0 0 1rem'>Authoritative Workflow scores come from the backend read model; this view only formats and drills down.</p>";
        html += '<div class="rs-table-wrap"><table class="rs-table">';
        html += "<thead><tr>";
        html += "<th>Model</th>";
        html += "<th>Configuration</th>";
        html += "<th>Status</th>";
        html += "<th>Checks passed / total</th>";
        html += "<th>Score</th>";
        html += "</tr></thead><tbody>";
        rows.forEach(function (row) {
            var comp = row.config.component_score || {};
            var frac = comp.fraction;
            var passed = na(comp.checks_passed);
            var total = na(comp.checks_total);
            var scorePct = (frac !== "\u2014") ? Math.round(frac * 100) : "\u2014";
            html += "<tr>";
            html += "<td>" + esc(row.model.model_family || row.model.model_version || "unknown") + "</td>";
            html += "<td><code>" + esc((row.config.configuration_fingerprint || "\u2014").slice(0, 12)) + "</code></td>";
            html += "<td>" + esc(row.config.status || "incomplete") + "</td>";
            html += "<td>" + esc(passed) + " / " + esc(total) + "</td>";
            html += "<td>" + (scorePct === "\u2014" ? "<span class='rs-na'>\u2014</span>" : scorePct + "%") + "</td>";
            html += "</tr>";
        });
        html += "</tbody></table></div></div>";
        container.innerHTML = html;
    }

    // -----------------------------------------------------------------------
    // Placeholder views (planned / research) -- never fake data.
    // -----------------------------------------------------------------------
    function renderPlaceholder(container, title, note) {
        if (!container) { return; }
        container.innerHTML =
            '<div class="rs-placeholder">' +
            "<div class='rs-placeholder-title'>" + esc(title) + "</div>" +
            "<p class='rs-placeholder-note'>" + esc(note) + "</p>" +
            "</div>";
    }

    // -----------------------------------------------------------------------
    // State fragments: loading / no-results / error banner.
    // -----------------------------------------------------------------------
    function stateLoading() {
        return '<div class="rs-state"><span class="rs-state-spinner" aria-hidden="true"></span>' +
            "<span>Loading&hellip;</span></div>";
    }

    function stateNoResults(text) {
        return '<div class="rs-state rs-state-noresults">' + esc(text) + "</div>";
    }

    function banner(kind, title, message) {
        var cls = "rs-banner rs-banner-" + kind;
        return '<div class="' + cls + '">' +
            "<span class='rs-banner-icon' aria-hidden='true'>" +
            (kind === "error" ? "\u2716" : kind === "warn" ? "\u26A0" : "\u2139") +
            "</span>" +
            "<div class='rs-banner-text'><span class='rs-banner-title'>" + esc(title) + "</span>" +
            esc(message) + "</div></div>";
    }

    // -----------------------------------------------------------------------
    // Context (0018) and Intelligence (research) placeholders.
    // Loaded eagerly so the tabs are populated without a round-trip.
    // -----------------------------------------------------------------------
    renderPlaceholder(
        document.getElementById("context-container"),
        "Context degradation results",
        "The Context benchmark exists, but its degradation Results UI is pending RM-26-AA-0018. This area will surface context-size progression and the degradation/drift curve once delivered."
    );
    renderPlaceholder(
        document.getElementById("intelligence-container"),
        "AI Intelligence benchmark",
        "Intelligence is a research item (RM-26-AA-0010). No benchmark contract or results UI exists yet; this area stays unavailable until then."
    );

    // -----------------------------------------------------------------------
    // Initial state: Overall view is active and loaded.
    // -----------------------------------------------------------------------
    loaded["view-overall"] = true;
    loadOverall();
})();
