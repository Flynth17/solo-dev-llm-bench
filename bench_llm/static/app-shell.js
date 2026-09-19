/**
 * Solo Dev LLM Bench — Persistent application shell (shared sidebar).
 *
 * This is the SINGLE SOURCE OF TRUTH for the left navigation. Every user-facing
 * page (/, /results, /speed/results/{id}, /v2/results/{id}) mounts an empty
 * <div id="app-sidebar-mount"> and loads this script; it injects the identical
 * sidebar once and manages active state from the current URL. There is no
 * duplicated sidebar markup in any HTML file — editing here updates every page.
 *
 * Design rules (shell/navigation refactoring only — no benchmark logic):
 *  - The left sidebar is persistent across the whole app; only the right content
 *    pane (.rs-main) changes when navigating.
 *  - Live dimensions (Overall / Speed / Workflow / Context) are enabled buttons; future /
 *    not-yet-delivered dimensions (Intelligence) carry rs-nav-item-disabled
 *    plus a native `disabled` attribute so they never receive clicks or focus.
 *  - Active location is exposed semantically via aria-current="page".
 *  - The standalone home escape hatch is intentionally gone: "Benchmarks / Run"
 *    in this sidebar is the single place to return to the launcher.
 */
(function () {
    "use strict";

    // -----------------------------------------------------------------------
    // Sidebar markup (single source of truth). Mirrors the delivered Results
    // shell styling (.results-shell / .rs-*); reused verbatim on every page.
    // Two semantic <nav> groups: BENCHMARKS (home) and RESULTS (dimensions).
    // Overall is marked active by default so a pre-JS snapshot still shows a
    // sensible location; JS refines it from the URL below.
    // -----------------------------------------------------------------------
    var SIDEBAR_HTML =
        '<aside class="rs-sidebar" aria-label="Application navigation">' +
        '  <div class="rs-brand">' +
        '    <span class="rs-brand-name">Solo Dev LLM Bench</span>' +
        '  </div>' +
        '  <nav class="rs-nav rs-nav-bench" aria-label="Benchmarks">' +
        '    <a href="/" class="rs-nav-item" data-route="benchmarks">Benchmarks / Run</a>' +
        '  </nav>' +
        '  <nav class="rs-nav" aria-label="Results">' +
        '    <button type="button" class="rs-nav-item rs-nav-item-active" data-view="overall" id="nav-overall" aria-current="page">' +
        '      Overall<span class="rs-nav-chip rs-chip-planned">No composite</span>' +
        '    </button>' +
        '    <button type="button" class="rs-nav-item" data-view="speed" id="nav-speed">' +
        '      Speed<span class="rs-nav-chip rs-chip-delivered">Delivered</span>' +
        '    </button>' +
        '    <button type="button" class="rs-nav-item" data-view="workflow" id="nav-workflow">' +
        '      Workflow<span class="rs-nav-chip rs-chip-delivered">Delivered</span>' +
        '    </button>' +
        '    <!-- Context backend exists (0009); its degradation Results UI is RM-26-AA-0018. Navigation + single-run detail pane established here; deeper curve/metrics/drill-down in later subtasks. -->' +
        '    <button type="button" class="rs-nav-item" data-view="context" id="nav-context">' +
        '      Context<span class="rs-nav-chip rs-chip-delivered">Delivered</span>' +
        '    </button>' +
        '    <!-- Intelligence is a research item (RM-26-AA-0010). -->' +
        '    <button type="button" class="rs-nav-item rs-nav-item-disabled" data-view="intelligence" id="nav-intelligence" aria-disabled="true" disabled>' +
        '      Intelligence<span class="rs-nav-chip rs-chip-research">Research</span>' +
        '    </button>' +
        '  </nav>' +
        '</aside>';

    function mount() {
        var el = document.getElementById("app-sidebar-mount");
        if (el) { el.innerHTML = SIDEBAR_HTML; }
        return el;
    }

    // Which Results dimension (or the Benchmarks home) matches the current URL?
    function activeLocation() {
        var path = location.pathname;
        if (path === "/" || path === "") { return "benchmarks"; }
        if (path === "/results") {
            var hash = location.hash.replace(/^#/, "");
            if (hash === "overall" || hash === "speed" || hash === "workflow" || hash === "context") { return hash; }
            return "overall"; // canonical default, matches shipped markup
        }
        if (/^\/speed\/results\//.test(path)) { return "speed"; }
        if (/^\/v2\/results\//.test(path)) { return "workflow"; }
        return "overall";
    }

    function clearActive() {
        var items = document.querySelectorAll(".rs-nav-item");
        Array.prototype.forEach.call(items, function (item) {
            item.removeAttribute("aria-current");
            item.classList.remove("rs-nav-item-active");
        });
    }

    function markActive(loc) {
        // Always reset first so exactly one nav item carries the active marker.
        clearActive();
        if (loc === "benchmarks") {
            var link = document.querySelector('.rs-nav-bench a[data-route="benchmarks"]');
            if (link) {
                link.setAttribute("aria-current", "page");
                link.classList.add("rs-nav-item-active");
            }
            return;
        }
        var btn = document.getElementById("nav-" + loc);
        if (btn) {
            btn.setAttribute("aria-current", "page");
            btn.classList.add("rs-nav-item-active");
        }
    }

    // Off-page navigation: a dimension button on a non-/results page jumps to the
    // Results shell with that tab selected. On /results itself, results-shell.js
    // owns in-page view switching, so we stay out of its way.
    function wireDimensionNav() {
        var items = document.querySelectorAll('.rs-nav-item[data-view]');
        Array.prototype.forEach.call(items, function (item) {
            var view = item.getAttribute("data-view");
            if (!view || item.classList.contains("rs-nav-item-disabled")) { return; }
            if (!location.pathname.startsWith("/results")) {
                item.addEventListener("click", function () {
                    location.href = "/results#" + view;
                });
            }
        });
    }

    mount();
    markActive(activeLocation());
    wireDimensionNav();
})();
