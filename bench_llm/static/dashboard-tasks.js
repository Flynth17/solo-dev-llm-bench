/** Solo Dev LLM Bench - Dashboard Task Manager. */

// ---------------------------------------------------------------------------
// DOM references (task-specific)
// ---------------------------------------------------------------------------
var taskTypeSelect = document.getElementById("task-type-select");
var taskNameInput = document.getElementById("task-name-input");
var createTaskBtn = document.getElementById("create-task-btn");
var taskListContainer = document.getElementById("task-list");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/** Cached task list for incremental updates. */
var _cachedTasks = [];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Load all tasks from the API and render them. */
async function loadTasks() {
    try {
        var resp = await fetch("/api/tasks");
        if (!resp.ok) return;
        var data = await resp.json();
        _cachedTasks = data.tasks || [];
        renderTasks();
    } catch (_) {
        taskListContainer.innerHTML = '<p style="color:#dc2626;">Failed to load tasks.</p>';
    }
}

/** Render the cached task list into the DOM. */
function renderTasks() {
    taskListContainer.innerHTML = "";
    if (_cachedTasks.length === 0) {
        taskListContainer.innerHTML = '<p style="color:#64748b;font-style:italic;">No tasks yet. Create one above.</p>';
        return;
    }
    for (var i = 0; i < _cachedTasks.length; i++) {
        var t = _cachedTasks[i];
        var div = document.createElement("div");
        div.className = "task-item" + (t.status ? " " + t.status : "");
        div.setAttribute("data-task-id", t.task_id);

        var nameSpan = document.createElement("span");
        nameSpan.className = "task-name";
        nameSpan.textContent = t.name || "Unnamed";
        div.appendChild(nameSpan);

        var badge = document.createElement("span");
        badge.className = "task-type-badge " + (t.task_type || "");
        badge.textContent = (t.task_type || "").toUpperCase();
        div.appendChild(badge);

        var statusSpan = document.createElement("span");
        statusSpan.className = "task-status " + (t.status || "");
        statusSpan.textContent = (t.status || "pending").toUpperCase();
        div.appendChild(statusSpan);

        var timeSpan = document.createElement("span");
        timeSpan.className = "task-timestamp";
        timeSpan.textContent = t.created_at ? formatTimestamp(t.created_at) : "";
        div.appendChild(timeSpan);

        var actionsDiv = document.createElement("div");
        actionsDiv.className = "task-actions";

        // Run button (only if not running/completed)
        if (t.status !== "running") {
            var runBtn = document.createElement("button");
            runBtn.className = "run-btn";
            runBtn.title = "Run benchmark";
            runBtn.textContent = "\u23F5"; // hourglass
            runBtn.addEventListener("click", (function(taskId) {
                return function() { runTask(taskId); };
            })(t.task_id));
            actionsDiv.appendChild(runBtn);
        }

        // Delete button
        var delBtn = document.createElement("button");
        delBtn.className = "delete-btn-task";
        delBtn.title = "Delete task";
        delBtn.textContent = "\u2716"; // ×
        delBtn.addEventListener("click", (function(taskId) {
            return function() { deleteTask(taskId); };
        })(t.task_id));
        actionsDiv.appendChild(delBtn);

        div.appendChild(actionsDiv);

        // Result display
        if (t.result) {
            var resultDiv = document.createElement("div");
            resultDiv.className = "task-result";
            resultDiv.innerHTML = renderTaskResult(t);
            div.appendChild(resultDiv);
        }

        taskListContainer.appendChild(div);
    }
}

/** Render a task result summary. */
function renderTaskResult(task) {
    var result = task.result || {};
    var aggregate = result.aggregate || {};
    var taskType = task.task_type || "";
    var scoreKey = taskType + "_score";
    var score = aggregate["avg_" + scoreKey] || aggregate[scoreKey] || null;
    var meetsMin = result["meets_minimum"] || aggregate[taskType + "_meets_minimum"] || false;

    var html = '<div style="display:flex;gap:1rem;flex-wrap:wrap;align-items:center;">';
    if (score !== null) {
        var cls = meetsMin ? "pass" : "fail";
        html += '<span class="task-result-score ' + cls + '">Score: ' + score + '</span>';
    }
    if (aggregate["avg_tokens_per_second"]) {
        html += '<span>TPS: <strong>' + aggregate["avg_tokens_per_second"] + '</strong></span>';
    }
    html += '</div>';
    return html;
}

/** Create a new task. */
async function createTask() {
    var taskType = taskTypeSelect.value;
    var taskName = taskNameInput.value.trim() || (taskType + " Task");

    var prompt = "";
    // Load default prompt for this type
    var defaultPrompts = {
        "markdown": "Write a short technical document about AI code assistants that includes: headings, an unordered list, an ordered list, a fenced code block with Python code, a table, a horizontal rule, and a blockquote. Keep it under 500 tokens.",
        "python": "Write a Python function called process_data that: 1. Takes a list[int] and a str as parameters with type hints 2. Has a docstring explaining its purpose 3. Filters the list to keep only even numbers 4. Returns a dict with the original length and filtered list 5. Include import statements. Keep it under 300 tokens.",
        "java": "Write a Java class called Person that: 1. Has a class declaration with Javadoc comment 2. Has private fields: name (String) and age (int) 3. Has a constructor with parameters 4. Has getter methods: getName() returns String, getAge() returns int 5. Has a setter method: setName(String name) 6. Includes import statements. Keep it under 300 tokens."
    };
    prompt = defaultPrompts[taskType] || "";

    try {
        var resp = await fetch("/api/tasks", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name: taskName,
                task_type: taskType,
                prompt: prompt
            })
        });

        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }

        var data = await resp.json();
        showStatus("Task created: " + taskName, "info");

        // Reload tasks
        loadTasks();

        // Clear name input
        taskNameInput.value = "";
    } catch (e) {
        showStatus("Failed to create task: " + e.message, "error");
    }
}

/** Run a benchmark for a task. */
async function runTask(taskId) {
    var task = null;
    for (var i = 0; i < _cachedTasks.length; i++) {
        if (_cachedTasks[i].task_id === taskId) {
            task = _cachedTasks[i];
            break;
        }
    }
    if (!task) {
        showStatus("Task not found.", "error");
        return;
    }

    var model = modelSelect.value.trim();
    if (!model) {
        showStatus("Please select a model first.", "error");
        return;
    }

    var config = {
        lm_studio_url: lmStudioUrlInput.value.replace(/\/$/, ""),
        model: model,
        max_tokens: parseInt(maxTokensInput.value, 10) || 500,
        temperature: parseFloat(temperatureInput.value) || 0,
        iterations: parseInt(iterationsInput.value, 10) || 3,
        hardware_label: hardwareLabelInput.value.trim(),
        execution_environment: executionEnvSelect.value,
        connection_type: connectionTypeSelect.value,
    };

    // Optimistically update UI
    for (var j = 0; j < _cachedTasks.length; j++) {
        if (_cachedTasks[j].task_id === taskId) {
            _cachedTasks[j].status = "running";
            break;
        }
    }
    renderTasks();

    try {
        var resp = await fetch("/api/tasks/" + encodeURIComponent(taskId) + "/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(config)
        });

        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }

        var data = await resp.json();
        showStatus("Task completed: " + task.name, "info");

        // Reload tasks to get result
        loadTasks();
    } catch (e) {
        showStatus("Task failed: " + e.message, "error");
        // Reload tasks to get updated status
        loadTasks();
    }
}

/** Delete a task. */
async function deleteTask(taskId) {
    if (!confirm("Delete this task?")) {
        return;
    }

    try {
        var resp = await fetch("/api/tasks/" + encodeURIComponent(taskId), {
            method: "DELETE"
        });

        if (!resp.ok) {
            var err = await resp.json().catch(function () { return {}; });
            throw new Error(err.detail || "HTTP " + resp.status);
        }

        showStatus("Task deleted.", "info");
        loadTasks();
    } catch (e) {
        showStatus("Failed to delete task: " + e.message, "error");
    }
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------

createTaskBtn.addEventListener("click", createTask);