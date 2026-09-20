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
    // Sidebar navigation (shared Results nav)
    // Live dimensions are keyboard-reachable buttons; future/not-delivered
    // dimensions (Context, Intelligence) carry the rs-nav-item-disabled class and a
    // native `disabled` attribute so they never receive clicks or keyboard focus.
    // -----------------------------------------------------------------------
    var items = Array.prototype.slice.call(
        document.querySelectorAll(".rs-nav-item")
    );
    var views = {};
    items.forEach(function (item) {
        var id = item.getAttribute("data-view");
        if (id) { views["view-" + id] = document.getElementById("view-" + id); }
    });

    function activateView(bareId) {
        // bareId is the raw data-view value ("speed"). The view containers are keyed
        // as "view-<id>" in the views map above, so reconstruct the key here. The
        // previous code compared data-view against the prefixed viewId, which never
        // matched and therefore cleared aria-current without ever setting it.
        var viewKey = "view-" + bareId;
        // Mark the active live nav item: aria-current="page" + emphasis class.
        items.forEach(function (item) {
            var on = item.getAttribute("data-view") === bareId;
            if (on) {
                item.setAttribute("aria-current", "page");
                item.classList.add("rs-nav-item-active");
            } else {
                item.removeAttribute("aria-current");
                item.classList.remove("rs-nav-item-active");
            }
        });
        // Toggle view visibility.
        Object.keys(views).forEach(function (key) {
            var el = views[key];
            if (!el) { return; }
            if (key === viewKey) { el.classList.add("rs-active"); }
            else { el.classList.remove("rs-active"); }
        });
    }

    // debounce helper for the text filter (avoids re-filtering on every keystroke).
    function debounce(fn, wait) {
        var t;
        return function () {
            var args = arguments, ctx = this;
            clearTimeout(t);
            t = setTimeout(function () { fn.apply(ctx, args); }, wait);
        };
    }

    items.forEach(function (item) {
        item.addEventListener("click", function () {
            // Disabled / not-yet-delivered dimensions never navigate.
            if (item.classList.contains("rs-nav-item-disabled") || item.hasAttribute("disabled")) {
                return;
            }
            var viewId = item.getAttribute("data-view");
            if (viewId) {
                activateView(viewId); // bare id; activateView reconstructs the "view-" key
                // Lazily load a view's data the first time it is opened.
                ensureLoaded(viewId);
            }
        });
    });

    // Which views need on-demand data loading, keyed by view id.
    var loaders = {
        "view-overall": loadOverall,
        "view-workflow": loadWorkflow,
        // Compare (RM-26-AA-0013 ST-003) is owned by results-compare.js, which loads
        // AFTER this file. The bridge defers to that script's idempotent init hook once
        // it has run; when the page opens directly at #compare, results-compare.js
        // self-initializes instead (this bridge is a no-op until then).
        "view-compare": function () {
            if (typeof window.__compareInit === "function") { window.__compareInit(); }
        }
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
        "agentic": "Workflow",
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

        // Composite status: honest "no approved composite score" state with an explicit
        // NO COMPOSITE badge so the Overall view reads as deliberately unavailable -- not
        // partially implemented. The frontend never recomputes or infers a score.
        html += '<div class="rs-panel">';
        html += '<div class="rs-panel-title">Overall</div>';
        html += "<span class='rs-badge rs-badge-unavailable' aria-label='No composite score'>NO COMPOSITE</span>";
        html += banner("unavailable", "Composite score unavailable",
            esc(composite.reason ||
                "Overall Solo Bench composite scoring contract is not yet approved; no Overall Solo Bench score is computed or inferred."));
        html += "</div>";

        // Per-dimension availability (authoritative from the read model). Approved,
        // implemented benchmark families render as available even before any run exists;
        // unimplemented families render N/A with their backend-provided reason. The
        // frontend performs no dimension recomputation -- status comes from /api/ranking.
        var dims = ranking.all_dimensions || [];
        var approved = new Set(composite.available_dimensions || []);
        var dimReasons = composite.dimension_reasons || {};
        // Per-model detail (reason text) when results exist; backend reason otherwise.
        var perModel = (ranking.models && ranking.models.length) ? (ranking.models[0].dimensions || {}) : {};
        html += '<div class="rs-panel">';
        html += '<div class="rs-panel-title">Available dimensions</div>';
        html += '<div class="rs-card-grid">';
        dims.forEach(function (dim) {
            var label = DIM_LABELS[dim] || dim;
            var isApproved = approved.has(dim);
            var reason = (perModel[dim] && perModel[dim].reason) || dimReasons[dim];
            var marker = isApproved ? "\u2713" : "\u25CB"; // ✓ / ○
            var cls = isApproved ? "rs-dim-ok" : "rs-dim-unavailable";
            var valueText = isApproved
                ? "<span class='rs-ok-value'>\u2713 Available</span>"
                : "<span class='rs-dim-status " + cls + "'>\u2014 " + esc(reason || "Not available") + "</span>";
            html += '<div class="rs-metric-card">';
            html += '<div class="rs-metric-label">' + esc(label) + "</div>";
            html += valueText;
            html += "</div>";
        });
        html += "</div></div>";

        container.innerHTML = html;
    }

    // Reusable L0 -> L1 drill-down primitive (model family/version -> configuration/
    // quantization -> run evidence). Retained as a Results-UI-foundation component with
    // its own styling (.rs-model-group) and contract, but intentionally NOT invoked on
    // the Overall composite-unavailable view: while no approved composite exists the
    // Overall page must read as deliberately unavailable, never a partial leaderboard.
    // Other surfaces that legitimately drill model -> configuration consume it directly.
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
    // Workflow run entries (authoritative grouping from /api/ranking). Built once, then
    // filtered client-side. The frontend formats/sorts/filters only -- it never recomputes
    // the score; failed-check counts are arithmetic over already-exposed per-suite
    // passed/total values, not a re-derivation of the benchmark result.
    var __wfEntries = [];
    var __wfFiltered = [];

    function loadWorkflow() {
        var container = document.getElementById("workflow-container");
        if (!container) { return; }
        container.innerHTML = stateLoading();
        // Reset filters on (re)load so a stale filter never hides data.
        __wfEntries = [];
        __wfFiltered = [];
        resetWorkflowFilters();

        fetch("/api/ranking")
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
            .then(function (data) {
                __wfEntries = buildWorkflowEntries(data);
                renderWorkflow(container, __wfEntries);
            })
            .catch(function () {
                container.innerHTML = banner("error", "Unable to load workflow results",
                    "The ranking read model could not be loaded. Try refreshing the page.");
            });
    }

    // Aggregate agentic evidence from the authoritative ranking into per-run entries.
    // Two distinct sources, kept visually and semantically separate:
    //   1. eligible (canonical) Workflow results -- carry an approved fraction + suite
    //      breakdown; rendered as normal results.
    //   2. incomplete-but-valid runs (agentic_diagnostic_runs) -- carry real check counts
    //      and per-suite evidence but NO approved fraction (N/A stays N/A); rendered with
    //      an explicit incomplete/diagnostic marker, never as a canonical result.
    // No scoring is done here; all numbers are authoritative from /api/ranking.
    function buildWorkflowEntries(ranking) {
        var entries = [];
        // (1) Eligible (canonical) Workflow results.
        (ranking.models || []).forEach(function (m) {
            (m.configurations || []).forEach(function (c) {
                if (c.benchmark_family !== "agentic") { return; }
                var ag = c.component_score && c.component_score.agentic;
                // Ineligible runs have no component score; they are surfaced below via
                // agentic_diagnostic_runs so they are never emitted as empty rows.
                if (!ag) { return; }
                var suites = (ag.per_suite || []).map(function (s) {
                    return {
                        suite: s.suite,
                        passed: _asInt(s.checks_passed),
                        total: _asInt(s.checks_total)
                    };
                });
                // Failed checks = sum of (total - passed) per suite. Pure presentation
                // arithmetic over backend-exposed numbers; the authoritative fraction is
                // taken verbatim from the read model below and never recomputed.
                var failedCount = 0;
                suites.forEach(function (s) {
                    if (s.total != null && s.passed != null && s.total > s.passed) {
                        failedCount += s.total - s.passed;
                    }
                });
                (c.run_ids || []).forEach(function (runId) {
                    entries.push({
                        model_version: m.model_version,
                        model_family: m.model_family || m.model_version || "unknown",
                        architecture: m.architecture || "unknown",
                        configuration_fingerprint: c.configuration_fingerprint || "\u2014",
                        run_id: runId,
                        status: c.status || "incomplete",
                        passed: ag.checks_passed,
                        total: ag.checks_total,
                        fraction: ag.fraction, // authoritative; rendered verbatim
                        suites: suites,
                        failedCount: failedCount
                    });
                });
            });
        });
        // (2) Incomplete-but-valid Workflow runs -- inspectable diagnostic evidence.
        // Real check counts + per-suite breakdown are surfaced, but fraction is explicitly
        // null so the view never claims an approved composite score for these runs.
        (ranking.agentic_diagnostic_runs || []).forEach(function (d) {
            var passed = _asInt(d.checks_passed);
            var total = _asInt(d.checks_total);
            entries.push({
                model_version: d.model_version,
                model_family: d.model_family || d.model_version || "unknown",
                architecture: d.architecture || "unknown",
                configuration_fingerprint: d.configuration_fingerprint || "\u2014",
                run_id: d.run_id,
                status: d.status || "incomplete",
                passed: passed,
                total: total,
                fraction: null, // diagnostic runs never carry an approved fraction
                suites: ((d.per_suite || []).map(function (s) {
                    return { suite: s.suite, passed: _asInt(s.checks_passed), total: _asInt(s.checks_total) };
                })),
                failedCount: (total != null && passed != null && total > passed) ? (total - passed) : 0,
                diagnostic: true
            });
        });
        // Failure-first ordering: runs with more failing checks surface at the top so the
        // correctness signal is never buried under perfect runs. Canonical and diagnostic
        // runs share this ordering; rendering keeps them visually distinct.
        entries.sort(function (a, b) { return b.failedCount - a.failedCount; });
        return entries;
    }

    function renderWorkflow(container, entries) {
        var bar = document.getElementById("workflow-filter-bar");
        if (bar) { bar.hidden = entries.length === 0; }
        var countEl = document.getElementById("workflow-count");

        if (entries.length === 0) {
            // Workflow family is delivered; zero runs yet -> no-results (not unavailable).
            if (bar) { bar.hidden = true; }
            container.innerHTML = stateNoResults(
                "No workflow results yet. Run a Workflow suite from the benchmark page to see its deterministic correctness result here.");
            return;
        }

        __wfFiltered = entries;
        applyWorkflowFilters();
    }

    // Apply the three filter controls (model text / run state / failures-only) and re-render.
    function applyWorkflowFilters() {
        var modelFilter = (document.getElementById("wf-filter-model") || {}).value || "";
        var statusFilter = (document.getElementById("wf-filter-status") || {}).value || "";
        var failuresOnly = (document.getElementById("wf-filter-failures") || {}).checked;

        var filtered = __wfEntries.filter(function (e) {
            if (statusFilter && e.status !== statusFilter) { return false; }
            if (failuresOnly && e.failedCount <= 0) { return false; }
            if (modelFilter) {
                var hay = ((e.model_family || "") + " " + (e.model_version || "")).toLowerCase();
                if (hay.indexOf(modelFilter.toLowerCase()) === -1) { return false; }
            }
            return true;
        });

        var countEl = document.getElementById("workflow-count");
        if (countEl) {
            countEl.textContent = filtered.length + " workflow run" + (filtered.length !== 1 ? "s" : "") +
                (__wfEntries.length > filtered.length ? " (of " + __wfEntries.length + ")" : "");
        }

        var container = document.getElementById("workflow-container");
        if (!container) { return; }
        if (filtered.length === 0) {
            container.innerHTML = stateNoResults("No workflow runs match the current filters.");
            return;
        }
        container.innerHTML = renderWorkflowRuns(filtered);
        wireWorkflowDrillDown();
    }

    function renderWorkflowRuns(entries) {
        var html = '<div class="rs-panel">';
        html += '<div class="rs-panel-title">Workflow results</div>';
        html += "<p class='rs-placeholder-note' style='margin:0 0 1rem'>Authoritative Workflow scores come from the backend read model; this view only formats, filters and drills down -- it never recomputes a score.</p>";
        html += "<div class='rs-wf-list'>";
        entries.forEach(function (e) { html += renderWorkflowRunRow(e); });
        html += "</div></div>";
        return html;
    }

    function renderWorkflowRunRow(e) {
        var scorePct = (typeof e.fraction === "number" && !isNaN(e.fraction)) ? Math.round(e.fraction * 100) : null;
        var passedTxt = (e.passed != null) ? String(e.passed) : "\u2014";
        var totalTxt = (e.total != null) ? String(e.total) : "\u2014";

        // Status chip: presentation semantics (label/tone/chip) come from the shared
        // results-status.js layer -- never recomputed here, never colour-only.
        var statusPres = statusPresentation(e.status);

        // Suite breakdown chips (authoritative per-suite passed/total).
        var suiteChips = "";
        (e.suites || []).forEach(function (s) {
            var label = s.suite ? esc(s.suite.replace(/_/g, " ")) : "suite";
            var val = (s.passed != null && s.total != null) ? (s.passed + "/" + s.total) : "\u2014";
            suiteChips += "<span class='rs-badge rs-badge-accent' title='" + label + "'>" + label + " " + esc(val) + "</span>";
        });

        // A perfect run (all checks passed) reads as a success; any failing check is flagged.
        // When the total is unknown (null), never claim "all passed" -- show an em-dash so
        // N/A stays distinct from a real pass (N/A is never coerced to a success).
        var failedChip;
        if (e.total == null) {
            failedChip = "<span class='rs-na'>\u2014</span>";
        } else if (e.failedCount > 0) {
            failedChip = "<span class='rs-badge rs-badge-unavailable' title='Failing checks'>" + e.failedCount + " failing</span>";
        } else {
            failedChip = "<span class='rs-badge rs-badge-ok'>All checks passed</span>";
        }

        var scoreTxt = (scorePct === null) ? "<span class='rs-na'>\u2014</span>" : scorePct + "%";

        return '<details class="rs-wf-run" open>' +
            '<summary>' +
                '<span class="rs-wf-head">' +
                    "<span class='rs-badge rs-badge-accent'>" + esc(e.model_family) + "</span>" +
                    "<code class='rs-wf-fp' title='Configuration fingerprint'>" + esc(String(e.configuration_fingerprint).slice(0, 12)) + "</code>" +
                    failedChip +
                    "<span class='rs-chip " + statusPres.className + "' title='" + esc(statusPres.explanation || "") + "'>" +
                        esc(statusPres.label) + "</span>" +
                "</span>" +
                '<span class="rs-wf-score">' +
                    "<span class='rs-wf-checks'>" + esc(passedTxt) + " / " + esc(totalTxt) + " checks<span class='rs-wf-score-pct'> · " + scoreTxt + "</span></span>" +
                "</span></summary>" +
            '<div class="rs-wf-body">' +
                (e.diagnostic ? "<p class='rs-wf-diagnostic'>Incomplete run \u2014 inspectable diagnostic evidence; no approved composite score is computed.</p>" : "") +
                "<div class='rs-wf-meta'><span>Run <code>" + esc(e.run_id) + "</code></span></div>" +
                (suiteChips ? "<div class='rs-wf-suites'>" + suiteChips + "</div>" : "") +
                "<a class='rs-view-link rs-wf-evidence' href='/v2/results/" + encodeURIComponent(e.run_id) + "' data-run-id='" + esc(e.run_id) + "'>View suite/case evidence \u2192</a>" +
            "</div></details>";
    }

    // Wire the filter controls (debounced model filter + change events) and clear button.
    function wireWorkflowFilters() {
        var modelInput = document.getElementById("wf-filter-model");
        if (modelInput) { modelInput.addEventListener("input", debounce(applyWorkflowFilters, 150)); }
        var statusSelect = document.getElementById("wf-filter-status");
        if (statusSelect) { statusSelect.addEventListener("change", applyWorkflowFilters); }
        var failuresBox = document.getElementById("wf-filter-failures");
        if (failuresBox) { failuresBox.addEventListener("change", applyWorkflowFilters); }
        var clearBtn = document.getElementById("wf-clear-filters");
        if (clearBtn) { clearBtn.addEventListener("click", resetWorkflowFilters); }
    }

    function resetWorkflowFilters() {
        var modelInput = document.getElementById("wf-filter-model");
        if (modelInput) { modelInput.value = ""; }
        var statusSelect = document.getElementById("wf-filter-status");
        if (statusSelect) { statusSelect.value = ""; }
        var failuresBox = document.getElementById("wf-filter-failures");
        if (failuresBox) { failuresBox.checked = false; }
        wireWorkflowFilters();
    }

    function wireWorkflowDrillDown() {
        // Nothing extra needed: drill-down links are native <a href> deep links to the
        // dedicated /v2/results/{run_id} evidence page (stable, bookmarkable).
    }

    function _asInt(v) {
        var n = Number(v);
        return typeof n === "number" && !isNaN(n) ? n : null;
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
    // Context (0018) and Intelligence (research) views.
    // Loaded eagerly so the tabs are populated without a round-trip.
    // -----------------------------------------------------------------------

    // Context degradation is delivered per run (RM-26-AA-0018). The ranking read
    // model does not yet project Context runs into an aggregate list, so this pane
    // shows an honest, grounded state: Context results open from a Context run's
    // detail page (/context/results/{run_id}). No data is invented; N/A stays N/A.
    function renderContextAggregate(container) {
        if (!container) { return; }
        container.innerHTML =
            '<div class="rs-panel">' +
            '<div class="rs-panel-title">Context degradation</div>' +
            "<p class='rs-placeholder-note'>" +
            "Context measures how a model\u2019s correctness and retention change as usable context grows (15K\u2013240K). Degradation/drift curves, baseline, retention and per-point evidence are shown on each Context run\u2019s detail page. Run a Context benchmark from the launcher, or open a Context run from its evidence to see the curve." +
            "</p>" +
            "</div>";
    }

    renderContextAggregate(document.getElementById("context-container"));
    renderPlaceholder(
        document.getElementById("intelligence-container"),
        "AI Intelligence benchmark",
        "Intelligence is a research item (RM-26-AA-0010). No benchmark contract or results UI exists yet; this area stays unavailable until then."
    );

    // -----------------------------------------------------------------------
    // Initial state: Overall view is active and loaded. A #view hash (e.g. the
    // sidebar navigated here from another page as /results#speed) selects that
    // dimension instead of the default Overall. Reuses the click handler above,
    // which activates the view AND lazily loads its data.
    // -----------------------------------------------------------------------
    // The compare view may carry selection params in the fragment
    // (#compare?a=<key>&b=<key>); only the view id before "?" activates the nav item.
    var hashView = location.hash.replace(/^#/, "").split("?")[0];
    if (hashView === "speed" || hashView === "workflow" || hashView === "context" || hashView === "compare") {
        var hashBtn = document.getElementById("nav-" + hashView);
        if (hashBtn) { hashBtn.click(); }
    }

    loaded["view-overall"] = true;
    loadOverall();
})();
