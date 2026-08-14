# Solo Dev LLM Bench — UI / Results Flow Audit Report

**Date:** 2026-08-14  
**Scope:** Benchmark defaults, Past Results page separation, API/persistence consistency  
**Status:** Investigation only — no files modified

---

## Confirmed Issue #1: Wrong Default Max Output Tokens (500 instead of 1024)

### Symptom
The Benchmark page displays `Max Output Tokens: 500` on first load instead of the expected `1024`.

### Root Cause
**settings.json is the source of truth for the default value.**  
At `config/settings.json` line 6:
```json
"max_tokens": 500,
```

The HTML input at `static/index.html` line 83 has the correct default:
```html
<input type="number" id="max-tokens" value="1024" min="1" max="10000">
```

However, `dashboard.js` line 147's `loadConfig()` overwrites the HTML default with the server-saved value:
```js
maxTokensInput.value = config.max_tokens || 1024;
```

The server reads from `config/settings.json` (via `config_loader.py`), so the value `500` from settings.json wins over the HTML default of `1024`.

### Data Flow
```
settings.json (max_tokens=500)
  → config_loader.py loads it
    → GET /api/config returns {"max_tokens": 500, ...}
      → dashboard.js loadConfig() sets maxTokensInput.value = 500
        → HTML default of 1024 is overwritten
```

### File(s)
- `bench_llm/config/settings.json` (line 6) — **root cause**
- `bench_llm/static/index.html` (line 83) — correct HTML default
- `bench_llm/static/dashboard.js` (line 147) — overwrites HTML default
- `bench_llm/src/config_loader.py` — reads settings.json

### Severity
**Medium** — Users who start fresh or clear localStorage will see 500. Users who previously ran a benchmark with a non-default value will see that saved value.

### Recommended Fix
Change `config/settings.json` line 6 from `"max_tokens": 500` to `"max_tokens": 1024`.

---

## Confirmed Issue #2: Historical Comparison Chart Visible During Task History

### Symptom
When switching to `Past Results → Task History → Markdown`, the `Historical Comparison` chart (benchmark-performance chart) remains visible below the Task History results.

### Root Cause
The `switchTab()` function in `results.js` (lines 845-868) handles hiding/showing `resultsPanel` and `taskHistorySection` but **does NOT hide the `chartsPanel`** when switching to Task History mode.

Relevant code at line 845-868:
```js
function switchTab(tab) {
    activeTab = tab;
    if (tab === "benchmarks") {
        tabBenchmarks.classList.add("active");
        tabTasks.classList.remove("active");
        resultsPanel.classList.remove("hidden");
        if (taskHistorySection) taskHistorySection.classList.add("hidden");
        // Re-render chart if it was cleared by tab switch
        if (chartsPanel.classList.contains("hidden") && filteredRuns.length > 0) {
            chartsPanel.classList.remove("hidden");
            renderHistoryCharts();
        }
    } else {
        tabTasks.classList.add("active");
        tabBenchmarks.classList.remove("active");
        resultsPanel.classList.add("hidden");
        if (taskHistorySection) taskHistorySection.classList.remove("hidden");
        // ← chartsPanel is NEVER hidden here!
        ...
    }
}
```

The `chartsPanel` is only hidden in `renderResults()` (line 197) when `filteredRuns.length === 0`. When Task History is active and there are benchmark results in `filteredRuns`, the chart remains visible.

### Data Flow
```
User clicks "Task History" tab
  → switchTab("tasks") called
    → resultsPanel.classList.add("hidden") ✓
    → taskHistorySection.classList.remove("hidden") ✓
    → chartsPanel untouched ✗
      → renderTasks() renders task rows ✓
      → renderResults() checks filteredRuns.length > 0
        → chartsPanel.classList.add("hidden") NOT called ✗
```

### File(s)
- `bench_llm/static/results.js` (lines 845-868) — **root cause**: missing `chartsPanel.classList.add("hidden")` in the `else` branch
- `bench_llm/static/results.html` (line 81) — charts-panel structure
- `bench_llm/static/style.css` — `.charts-panel` visibility rules

### Severity
**Low** — Visual confusion only. The chart is not interactive and sits below task results. Does not affect data.

### Recommended Fix
Add `chartsPanel.classList.add("hidden")` in the `switchTab("tasks")` else branch, and add `chartsPanel.classList.remove("hidden")` + chart re-render in the `switchTab("benchmarks")` if branch when `filteredRuns.length > 0`.

---

## Confirmed Issue #3: Task History Data Presentation — All Metrics Available

### Symmetry Check: All Stored Metrics
The `task_runs` table stores these columns (from `task_manager.py`):
```
task_id, task_name, task_type, model, timestamp,
passed, score,
initial_errors, final_errors, errors_fixed,
output_tokens, input_tokens, tokens_per_second, ttft_seconds, wall_time_seconds,
result (JSON), created_at
```

### API Response (`/api/tasks-with-results`)
The API returns all task_runs fields as top-level properties on each task object:
```json
{
  "tasks": [
    {
      "id": 1,
      "task_id": "task-...",
      "task_name": "Markdownlint Default",
      "task_type": "markdown",
      "model": "glm-4.7-flash",
      "timestamp": "2026-08-14T21:42:00Z",
      "passed": true,
      "score": 1.0,
      "initial_errors": 23,
      "final_errors": 0,
      "errors_fixed": 23,
      "output_tokens": 512,
      "input_tokens": 256,
      "tokens_per_second": 238.0,
      "ttft_seconds": 0.45,
      "wall_time_seconds": 2.15,
      "result": {"passed": true, "score": 1.0},
      "created_at": "2026-08-14T21:42:00Z"
    }
  ]
}
```

### Currently Displayed in Task History Row
`results.js` `renderTasks()` (lines 896+) displays:
| Metric | Status |
|--------|--------|
| task name | ✓ displayed as name badge |
| task type | ✓ displayed as type badge (Markdown/Python/Java/Unsolvable) |
| model | ✓ displayed as model badge |
| PASS/FAIL | ✓ displayed as score badge (green/red) |
| score | ✓ displayed as score value |
| initial errors | ✗ NOT displayed in row (stored in DB, returned by API) |
| final errors | ✗ NOT displayed in row (stored in DB, returned by API) |
| errors fixed | ✗ NOT displayed in row (stored in DB, returned by API) |
| tok/s | ✗ NOT displayed in row (stored in DB, returned by API) |
| TTFT | ✗ NOT displayed in row (stored in DB, returned by API) |
| input tokens | ✗ NOT displayed in row (stored in DB, returned by API) |
| output tokens | ✗ NOT displayed in row (stored in DB, returned by API) |
| wall time | ✗ NOT displayed in row (stored in DB, returned by API) |
| timestamp | ✓ displayed as timestamp badge |

### Metrics Available in Expandable Details
The task-type filter bar and Markdown result cards display:
- When `task_type === "markdown"`, clicking a row expands a Markdown result card showing:
  - initial_errors, final_errors, errors_fixed
  - score, passed
  - tokens_per_second, ttft_seconds, output_tokens, input_tokens, wall_time_seconds

### Data Loss Assessment
**No data is lost.** All metrics flow correctly:
```
Persistence (task_runs table) → API (/api/tasks-with-results) → Frontend (renderTasks)
```

The only gap is that **detailed metrics are only visible in the expandable Markdown result card**, not in the default task row. For Python/Java/Unsolvable tasks, the expandable section shows a generic summary without error-specific fields.

### File(s)
- `bench_llm/src/task_manager.py` (get_task_runs) — persistence layer
- `bench_llm/src/main.py` (get_tasks_with_results endpoint) — API layer
- `bench_llm/static/results.js` (renderTasks, renderMarkdownResultCard) — frontend layer
- `bench_llm/static/style.css` (.task-history-row, .markdown-result-card) — styling

### Severity
**Low** — All data is available in the expandable details view. Users who don't expand rows won't see detailed metrics.

---

## Confirmed Issue #4: Tab / Filter State Management

### Default Tab
**Confirmed:** Default is `Task History` (line 108 of results.js):
```js
var activeTab = "tasks"; // "benchmarks" or "tasks"
```

### Default Task-Type Filter
**Confirmed:** Default is `All` (line 111):
```js
var activeTaskType = "all";
```

### Tab Switching Hides Unrelated Sections
**Partially working:**
- `switchTab("tasks")` hides `resultsPanel` ✓
- `switchTab("tasks")` shows `taskHistorySection` ✓
- `switchTab("tasks")` does NOT hide `chartsPanel` ✗ (Issue #2)
- `switchTab("benchmarks")` hides `taskHistorySection` ✓
- `switchTab("benchmarks")` shows `resultsPanel` ✓

### Task Filter Affects Only Task History
**Confirmed:** Task-type filter only modifies `activeTaskType` and calls `loadTasks()` which queries `/api/tasks-with-results?task_type=...`. The benchmark results are unaffected. ✓

### State Leaks Between Task History and Benchmark History
**Confirmed:** The only leak is `chartsPanel` visibility (Issue #2). The `filteredRuns` array is shared between both views, which is intentional for the chart.

### Refresh Produces Expected Default State
**Confirmed:** On page load:
- `activeTab = "tasks"` → Task History tab is active ✓
- `activeTaskType = "all"` → All task types shown ✓
- `loadTasks()` called → all tasks loaded ✓
- `switchTab("tasks")` called → Task History section shown ✓

---

## Confirmed Issue #5: Historical Comparison Logic

### Grouping by Model
**Confirmed:** `renderGroupedModelChart()` (line 401) groups by `model_key` from `filteredRuns`. ✓

### Best Result Selection
**Confirmed:** Each model's `.best` is selected by highest `avgWarmTps` (warm run avg tok/s). ✓

### Fastest → Slowest Sorting
**Confirmed:** `groupedModels` is sorted descending by `best.avgWarmTps`. ✓

### Expand / Collapse
**Confirmed:** Expand arrow (`▸`) is shown for models with multiple runs. Clicking toggles `others` results visibility. ✓

### Responsive Sizing
**Confirmed:** Chart width computed from `chartsContainer.clientWidth` with margin adjustments. SVG scales via `width: 100%`. ✓

### Model-Name Handling
**Confirmed:** Model names displayed as `model_key` (e.g. "qwen-coder-4bit"). No display_name fallback. ✓

### Repeated Runs Preservation
**Confirmed:** All iterations are preserved in SQLite. The `filteredRuns` array contains all rows. Grouping only affects chart display. ✓

---

## Confirmed Issue #6: API / Persistence Consistency

### Performance Benchmark Flow
```
Benchmark run
  → ResultsStore.add_result() (results.py)
    → SQLite benchmark_results.runs table (via SQLite connection)
      → GET /api/benchmark/results (main.py)
        → Dashboard.js loadResults() → renderHistory()
          → Benchmark History tab (Past Results)
          → Historical Comparison chart (Past Results)
```

### Workflow Task Flow
```
Task execution
  → task_manager.create_task_run() (task_manager.py)
    → SQLite benchmark_results.task_runs table
      → GET /api/tasks-with-results (main.py)
        → results.js loadTasks() → renderTasks()
          → Task History tab (Past Results)
```

### Mixing Points
**No accidental mixing detected.** The two result types query separate tables:
- Benchmark results: `GET /api/results` → `ResultsStore.get_all()` → `runs` table
- Task results: `GET /api/tasks-with-results` → `task_manager.get_task_runs()` → `task_runs` table

The only shared element is `chartsPanel` in the DOM, which is used for benchmark charts only. Task History results are rendered into `task-history-container` (separate from `charts-container`).

---

## Expected Behaviour Summary

```
┌─────────────────────────────────────────────────────┐
│  Benchmark Page (/)                                 │
│  ┌─────────────────────────────────────────────────┐│
│  │  Configuration (iterations, max_tokens, ...)    ││
│  │  Model selector, prompt, task manager           ││
│  │  [Run Benchmark] button                          ││
│  └─────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────┐│
│  │  Current Run Output                             ││
│  │  Task execution results                         ││
│  └─────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  Past Results Page (/results)                       │
│  ┌─────────────────────────────────────────────────┐│
│  │  [Task History] [Benchmark History]  (default:  ││
│  │   Task History)                                 ││
│  └─────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────┐│
│  │  All | Markdown | Python | Java | Unsolvable    ││
│  │  (default: All)                                 ││
│  └─────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────┐│
│  │  Task History (default tab)                     ││
│  │  → Workflow task results only                   ││
│  │  → Markdown cards with expandable details       ││
│  │  → No benchmark chart                           ││
│  └─────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────┐│
│  │  Benchmark History                              ││
│  │  → Past benchmark runs                          ││
│  │  → Historical Comparison chart                  ││
│  │  → Performance metrics only                     ││
│  └─────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────┘
```

---

## No-Code Recommendation: Smallest Fix Sequence

### Fix 1: Restore Correct Default Max Output Tokens
**File:** `bench_llm/config/settings.json`  
**Change:** Line 6 — `"max_tokens": 500` → `"max_tokens": 1024`

This is a one-line fix. After this change, `loadConfig()` in dashboard.js will return the correct default.

---

### Fix 2: Hide Charts Panel When Showing Task History
**File:** `bench_llm/static/results.js`  
**Change:** In `switchTab("tasks")` else branch (around line 857), add:
```js
chartsPanel.classList.add("hidden");
```

And in `switchTab("benchmarks")` if branch (around line 853), ensure chart is shown when there are results:
```js
if (filteredRuns.length > 0) {
    chartsPanel.classList.remove("hidden");
}
```

This ensures the Historical Comparison chart only appears when Benchmark History tab is active.

---

### Fix 3 (Optional): Show More Metrics in Default Task Row
**File:** `bench_llm/static/results.js`  
**Change:** In `renderTasks()`, add `tokens_per_second` (tok/s) to the default task row alongside the existing badges. This makes the most commonly-used metric visible without requiring expansion.

---

## No Additional Issues Found

- Historical Comparison chart logic is sound (grouping, sorting, expand/collapse, responsive sizing)
- Task History data presentation is complete (all metrics flow correctly through persistence → API → UI)
- Tab/filter state management works correctly on default load
- API/persistence separation is clean (no accidental mixing of result types)