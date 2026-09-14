/** Solo Dev LLM Bench - Results page navigation (Standard Speed history only). */

// Navigation DOM references
var navRawSpeed = document.getElementById("nav-raw-speed");
var filterBar = document.getElementById("filter-bar");

// ---------------------------------------------------------------------------
// Navigation (Raw Speed only)
// ---------------------------------------------------------------------------

function switchView() {
    if (navRawSpeed) {
        navRawSpeed.classList.add("active");
    }
    // The Results page is a single view (Standard Speed history): always surface the
    // filter bar. Past-runs panel visibility is owned by results.js renderResults().
    if (filterBar) { filterBar.classList.remove("hidden"); }
}

// Wire up the single Raw Speed tab and surface the filter bar on load.
(function () {
    var btn = navRawSpeed;
    if (btn) {
        btn.addEventListener("click", function () { switchView(); });
    }
    switchView();
})();
