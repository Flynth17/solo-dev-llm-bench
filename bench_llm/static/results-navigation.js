/** Solo Dev LLM Bench - Results page navigation logic (extracted). */

// Navigation DOM references
var navRawSpeed = document.getElementById("nav-raw-speed");
var navMarkdown = document.getElementById("nav-markdown");
var navPython = document.getElementById("nav-python");
var navJava = document.getElementById("nav-java");
var navUnsolvable = document.getElementById("nav-unsolvable");
var taskHistorySection = document.getElementById("task-history-section");

// Active view — default to "raw" (Raw Speed)
var activeView = "raw"; // "raw", "markdown", "python", "java", "unsolvable"

// ---------------------------------------------------------------------------
// Navigation (Raw Speed | Markdown | Python | Java | Unsolvable)
// ---------------------------------------------------------------------------

function switchView(view) {
    activeView = view;

    // Update active button
    var navButtons2 = [navRawSpeed, navMarkdown, navPython, navJava, navUnsolvable];
    for (var i = 0; i < navButtons2.length; i++) {
        if (navButtons2[i]) navButtons2[i].classList.remove("active");
    }
    switch (view) {
        case "raw": if (navRawSpeed) navRawSpeed.classList.add("active"); break;
        case "markdown": if (navMarkdown) navMarkdown.classList.add("active"); break;
        case "python": if (navPython) navPython.classList.add("active"); break;
        case "java": if (navJava) navJava.classList.add("active"); break;
        case "unsolvable": if (navUnsolvable) navUnsolvable.classList.add("active"); break;
    }

    if (view === "raw") {
        // Raw Speed mode — show performance data, hide task results
        if (taskHistorySection) taskHistorySection.classList.add("hidden");
        if (filterBar) filterBar.classList.remove("hidden");
        resultsPanel.classList.remove("hidden");
        if (chartsPanel) chartsPanel.classList.remove("hidden");
        // Re-render chart
        if (filteredRuns.length > 0) {
            renderHistoryCharts();
        }
    } else {
        // Task results mode — hide performance data, show task results
        if (filterBar) filterBar.classList.add("hidden");
        resultsPanel.classList.add("hidden");
        if (chartsPanel) chartsPanel.classList.remove("hidden");
        // Set active task type from view
        activeTaskType = view;
        // Load and render tasks + comparison chart for correctness tabs.
        allTasks = [];
        loadTasks().then(function () {
            renderCorrectnessComparisonChart(allTasks);
            renderTasks();
        });
    }
}

// Attach click handlers to all nav buttons
var _navBtns = [navRawSpeed, navMarkdown, navPython, navJava, navUnsolvable];
for (var ni = 0; ni < _navBtns.length; ni++) {
    (function (btn) {
        if (btn) {
            btn.addEventListener("click", function () {
                var type = btn.getAttribute("data-type");
                switchView(type);
            });
        }
    })(_navBtns[ni]);
}