/** Solo Dev LLM Bench - Task History logic (extracted from results.js). */

// ---------------------------------------------------------------------------
// Task History state
// ---------------------------------------------------------------------------

var allTasks = [];
var activeTaskType = "all"; // "all", "markdown", "python", "java", "unsolvable"

// ---------------------------------------------------------------------------
// Task History
// ---------------------------------------------------------------------------

async function loadTasks() {
    try {
        var url = "/api/tasks-with-results";
        if (activeTaskType !== "all") {
            url += "?task_type=" + encodeURIComponent(activeTaskType);
        }
        var resp = await fetch(url);
        if (!resp.ok) return;
        var data = await resp.json();
        allTasks = data.tasks || [];
    } catch (_) {
        allTasks = [];
    }
}

function renderTasks() {
    var container = document.getElementById("task-history-container");
    if (!container) return;
    container.innerHTML = "";
    var taskHistorySection = document.getElementById("task-history-section");
    var emptyEl = document.getElementById("task-history-empty");

    if (allTasks.length === 0) {
        if (taskHistorySection) taskHistorySection.classList.remove("visible");
        if (taskHistorySection) taskHistorySection.classList.add("hidden");
        if (emptyEl) emptyEl.classList.remove("hidden");
        return;
    }

    if (emptyEl) emptyEl.classList.add("hidden");
    if (taskHistorySection) {
        taskHistorySection.classList.remove("hidden");
        taskHistorySection.classList.add("visible");
    }

    for (var i = 0; i < allTasks.length; i++) {
        var t = allTasks[i];
        var taskType = t.task_type || "";
        var passed = t.passed;
        var score = t.score;
        var initialErrors = t.initial_errors;
        var finalErrors = t.final_errors;
        var errorsFixed = t.errors_fixed;
        var tps = t.tokens_per_second;
        var model = t.model || "";
        var timestamp = t.timestamp || t.created_at || "";

        var div = document.createElement("div");
        div.className = "task-history-row";
        div.setAttribute("data-task-id", t.task_id);
        div.setAttribute("data-run-id", t.id);

        // Name
        var nameSpan = document.createElement("span");
        nameSpan.className = "task-history-name";
        nameSpan.textContent = t.task_name || "Unnamed";
        nameSpan.title = t.task_name || "Unnamed";
        div.appendChild(nameSpan);

        // Type badge
        var typeSpan = document.createElement("span");
        typeSpan.className = "task-history-type " + taskType;
        typeSpan.textContent = taskType.toUpperCase();
        div.appendChild(typeSpan);

        // Model
        if (model) {
            var modelSpan = document.createElement("span");
            modelSpan.className = "badge badge-model";
            modelSpan.textContent = model;
            modelSpan.title = "Model";
            div.appendChild(modelSpan);
        }

        // PASS/FAIL badge — use the stored passed value from the validator/task runner
        var effectivePass = (passed !== null && passed !== undefined) ? passed : false;
        if (effectivePass) {
            var statusBadge = document.createElement("span");
            statusBadge.className = "task-history-score pass";
            statusBadge.textContent = "PASS";
            statusBadge.title = "Task passed";
            div.appendChild(statusBadge);
        }

        // Score as percentage (stored 0.0-1.0, displayed as 0-100%)
        if (score !== null && score !== undefined) {
            var scoreSpan = document.createElement("span");
            scoreSpan.className = "task-history-score " + (effectivePass ? "pass" : "fail");
            scoreSpan.textContent = (score * 100).toFixed(0) + "%";
            scoreSpan.title = "Score: " + score.toFixed(4) + " (stored)";
            div.appendChild(scoreSpan);
        }

        // TPS
        if (tps !== null && tps !== undefined) {
            var tpsSpan = document.createElement("span");
            tpsSpan.className = "task-history-tps";
            tpsSpan.textContent = fmt2(tps) + " tok/s";
            div.appendChild(tpsSpan);
        }

        // Timestamp
        var tsSpan = document.createElement("span");
        tsSpan.className = "task-history-timestamp";
        tsSpan.textContent = formatTimestamp(timestamp);
        div.appendChild(tsSpan);

        // Actions
        var actionsDiv = document.createElement("div");
        actionsDiv.className = "task-history-actions";

        var delBtn = document.createElement("button");
        delBtn.className = "delete-task-btn";
        delBtn.title = "Delete this task run";
        delBtn.textContent = "\u2716";
        delBtn.addEventListener("click", (function (runId) {
            return function () { deleteTaskRun(runId); };
        })(t.id));
        actionsDiv.appendChild(delBtn);

        div.appendChild(actionsDiv);
        container.appendChild(div);

        // Empty state messages for types with no results
        if (allTasks.length === 0 && activeTaskType !== "all" && activeTaskType !== "markdown") {
            var emptyMsg = "";
            if (activeTaskType === "python") emptyMsg = "No Python results yet.";
            else if (activeTaskType === "java") emptyMsg = "No Java results yet.";
            else if (activeTaskType === "unsolvable") emptyMsg = "No Unsolvable results yet.";
            if (emptyEl && emptyMsg) {
                emptyEl.textContent = emptyMsg;
                emptyEl.classList.remove("hidden");
            }
        }

        // Markdown result card (expandable detail)
        if (taskType === "markdown") {
            var card = document.createElement("div");
            card.className = "markdown-result-card" + (passed === false ? " fail" : "");
            card.style.display = "none";

            var header = document.createElement("div");
            header.className = "markdown-result-header";

            var passBadge = document.createElement("span");
            passBadge.className = passed ? "pass-badge" : "fail-badge";
            passBadge.textContent = passed ? "PASS" : "FAIL";

            var scoreVal = document.createElement("span");
            scoreVal.className = "markdown-result-score";
            scoreVal.textContent = "Score: " + (score !== null && score !== undefined ? (score * 100).toFixed(0) + "%" : "N/A");

            header.appendChild(passBadge);
            header.appendChild(scoreVal);
            card.appendChild(header);

            var body = document.createElement("div");
            body.className = "markdown-result-body";

            // Errors
            var errorsItem = document.createElement("div");
            errorsItem.className = "markdown-result-item";
            var errorsLabel = document.createElement("span");
            errorsLabel.className = "label";
            errorsLabel.textContent = "Errors";
            var errorsValue = document.createElement("span");
            errorsValue.className = "markdown-result-errors";
            errorsValue.textContent = (initialErrors !== null && initialErrors !== undefined ? initialErrors : "\u2014") + " \u2192 " + (finalErrors !== null && finalErrors !== undefined ? finalErrors : "\u2014");
            errorsItem.appendChild(errorsLabel);
            errorsItem.appendChild(errorsValue);
            body.appendChild(errorsItem);

            // Errors fixed
            var fixedItem = document.createElement("div");
            fixedItem.className = "markdown-result-item";
            var fixedLabel = document.createElement("span");
            fixedLabel.className = "label";
            fixedLabel.textContent = "Errors fixed";
            var fixedValue = document.createElement("span");
            fixedValue.className = "value";
            fixedValue.textContent = errorsFixed !== null && errorsFixed !== undefined ? errorsFixed : "\u2014";
            fixedItem.appendChild(fixedLabel);
            fixedItem.appendChild(fixedValue);
            body.appendChild(fixedItem);

            // Output tokens
            var outputTokens = t.output_tokens;
            if (outputTokens !== null && outputTokens !== undefined) {
                var outItem = document.createElement("div");
                outItem.className = "markdown-result-item";
                var outLabel = document.createElement("span");
                outLabel.className = "label";
                outLabel.textContent = "Output tokens";
                var outVal = document.createElement("span");
                outVal.className = "value";
                outVal.textContent = outputTokens;
                outItem.appendChild(outLabel);
                outItem.appendChild(outVal);
                body.appendChild(outItem);
            }

            // Input tokens
            var inputTokens = t.input_tokens;
            if (inputTokens !== null && inputTokens !== undefined) {
                var inItem = document.createElement("div");
                inItem.className = "markdown-result-item";
                var inLabel = document.createElement("span");
                inLabel.className = "label";
                inLabel.textContent = "Input tokens";
                var inVal = document.createElement("span");
                inVal.className = "value";
                inVal.textContent = inputTokens;
                inItem.appendChild(inLabel);
                inItem.appendChild(inVal);
                body.appendChild(inItem);
            }

            // Wall time
            var wallTime = t.wall_time_seconds;
            if (wallTime !== null && wallTime !== undefined) {
                var wallItem = document.createElement("div");
                wallItem.className = "markdown-result-item";
                var wallLabel = document.createElement("span");
                wallLabel.className = "label";
                wallLabel.textContent = "Wall time";
                var wallVal = document.createElement("span");
                wallVal.className = "value";
                wallVal.textContent = fmt2(wallTime) + " s";
                wallItem.appendChild(wallLabel);
                wallItem.appendChild(wallVal);
                body.appendChild(wallItem);
            }

            card.appendChild(body);

            // Expand button
            var expandBtn = document.createElement("button");
            expandBtn.className = "markdown-result-expand";
            expandBtn.textContent = "\u25BC Details";
            (function (card, btn) {
                btn.addEventListener("click", function () {
                    var extra = card.querySelector(".markdown-result-extra");
                    if (extra) {
                        extra.classList.toggle("visible");
                        btn.textContent = extra.classList.contains("visible") ? "\u25B6 Details" : "\u25BC Details";
                    }
                });
            })(card, expandBtn);
            card.appendChild(expandBtn);

            // Extra details (TTFT)
            var extra = document.createElement("div");
            extra.className = "markdown-result-extra";
            var ttft = t.ttft_seconds;
            if (ttft !== null && ttft !== undefined) {
                var ttftItem = document.createElement("div");
                ttftItem.className = "markdown-result-item";
                var ttftLabel = document.createElement("span");
                ttftLabel.className = "label";
                ttftLabel.textContent = "TTFT";
                var ttftVal = document.createElement("span");
                ttftVal.className = "value";
                ttftVal.textContent = formatTtft(ttft);
                ttftItem.appendChild(ttftLabel);
                ttftItem.appendChild(ttftVal);
                extra.appendChild(ttftItem);
            }
            card.appendChild(extra);

            container.appendChild(card);
        }
    }
}

async function deleteTaskHistory(taskId) {
    if (!confirm("Delete this task?")) return;

    try {
        var resp = await fetch("/api/tasks/" + encodeURIComponent(taskId), {
            method: "DELETE"
        });
        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }
        // Reload
        allTasks = [];
        loadTasks().then(function () { renderTasks(); });
    } catch (e) {
        alert("Failed to delete task: " + e.message);
    }
}

async function deleteTaskRun(runId) {
    if (!confirm("Delete this task run?")) return;

    try {
        var resp = await fetch("/api/task-runs/" + encodeURIComponent(runId), {
            method: "DELETE"
        });
        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }
        // Reload
        allTasks = [];
        loadTasks().then(function () { renderTasks(); });
    } catch (e) {
        alert("Failed to delete task run: " + e.message);
    }
}

// ---------------------------------------------------------------------------
// Task-type filter
// ---------------------------------------------------------------------------

var taskTypeFilter = document.getElementById("task-type-filter");
var taskTypeBtns = taskTypeFilter ? taskTypeFilter.querySelectorAll(".task-type-btn") : [];

for (var bi = 0; bi < taskTypeBtns.length; bi++) {
    (function (btn) {
        btn.addEventListener("click", function () {
            // Update active state
            for (var j = 0; j < taskTypeBtns.length; j++) {
                taskTypeBtns[j].classList.remove("active");
            }
            btn.classList.add("active");
            activeTaskType = btn.getAttribute("data-type");
            // Reload and re-render
            allTasks = [];
            loadTasks().then(function () { renderTasks(); });
        });
    })(taskTypeBtns[bi]);
}