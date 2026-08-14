# Solo Dev LLM Bench — Repository Audit Report

**Date:** 2026-08-14
**Scope:** Read-only audit. No files modified.
**Branch:** master (no commits yet — first push pending)

---

## 1. Current State — What Already Exists and Works

### 1.1 Benchmark Execution Flow
- **Fully functional.** The core benchmark loop lives in `src/benchmark.py`.
- Calls LM Studio's `/api/v1/chat` endpoint with `stream=False`.
- Reads `stats.time_to_first_token_seconds`, `stats.tokens_per_second`, `stats.input_tokens`, and `stats.total_output_tokens` from the JSON response body.
- Supports configurable iterations (1–100), max tokens (1–10000), and temperature (0–2).
- Classifies iteration 1 as "cold" and iterations 2+ as "warm" (heuristic, documented as such).
- Computes overall and warm-only aggregates (avg/min/max tokens/sec, avg TTFT).

### 1.2 TTFT and Tokens/sec Measurement
- **Fully functional.** Both metrics are extracted directly from LM Studio's `stats` object in the chat API response.
- TTFT is recorded as `time_to_first_token_seconds` (seconds as float).
- Tokens/sec is recorded as `tokens_per_second` (float).
- Wall-clock time is measured with `time.perf_counter()`.

### 1.3 Result Storage and CSV Handling
- **Fully functional.** `src/results.py` implements `ResultsStore` class.
- In-memory list of benchmark runs with CSV persistence at `data/benchmark_results.csv`.
- CSV schema matches README documentation (18 columns, well-documented).
- Automatic numeric conversion on read (int/float parsing).
- CSV headers created automatically if file doesn't exist.
- `results.json` is mentioned as legacy and left untouched.

### 1.4 Dashboard / API Implementation
- **Fully functional.** FastAPI backend (`src/main.py`, 340 lines) with the following endpoints:
  - `GET /` — Dashboard HTML page
  - `GET /api/config` / `POST /api/config` — Configuration management
  - `GET /api/models` — Fetch models from LM Studio
  - `POST /api/benchmark/run` — Run benchmark
  - `GET /api/benchmark/results` — Get all saved results
  - `GET /api/benchmark/runs/grouped` — Results grouped by run_id (for dashboard)
  - `GET/POST/PUT/DELETE /api/prompts/*` — Full CRUD for prompt presets
- **Frontend** (`static/index.html`, `static/dashboard.js`, `static/style.css`):
  - Complete dashboard UI with model selection, prompt presets, iteration settings.
  - SVG-based charts (tokens/sec by iteration, TTFT by iteration, historical comparison).
  - Prompt preset management (save, rename, delete).
  - Execution environment and hardware label tracking.
  - Timestamp formatting with timezone normalization.

### 1.5 Existing Benchmark/Test Abstractions
- **`run_benchmark()` in `src/benchmark.py`** — The sole benchmark abstraction. It:
  - Takes a single prompt string and runs N iterations against LM Studio.
  - Returns a structured dict with per-iteration results and aggregates.
- **Prompt presets** (`data/prompts.json` + CRUD API) — Allows saving/reusing prompts.
- **`ResultsStore` in `src/results.py`** — In-memory store with CSV persistence.

### 1.6 Existing Support for v1.1 Features
| Feature | Status | Notes |
|---------|--------|-------|
| Deterministic scoring | **None** | No scoring infrastructure exists. |
| File-based tasks | **None** | Prompts are text-only strings. |
| Validators/scorers | **None** | No validator interface or runner. |
| Retries / multiple attempts | **None** | No retry logic exists. |
| Python execution | **None** | No code execution infrastructure. |
| Markdown validation | **None** | No markdownlint integration. |
| Test framework integration | **None** | No pytest/JUnit integration. |

---

## 2. Partial / Reusable

### 2.1 Infrastructure to Reuse
- **`ResultsStore`** — CSV persistence is solid. The schema could be extended with new columns (e.g., `score`, `status`, `validator`) without breaking existing data.
- **Prompt preset system** — The CRUD API and `data/prompts.json` format could be repurposed or extended for "task" definitions.
- **`run_benchmark()` return structure** — The dict structure with `runs[]`, `aggregate`, and `warm_aggregate` is clean and reusable.
- **Configuration system** (`config/settings.json` + `config_loader.py`) — Simple and extensible.
- **Dashboard UI** — The chart rendering and results display are well-structured. SVG charts are self-contained and don't need external dependencies.
- **`server_launcher.py`** — Windows path setup is clean and handles the `uvicorn --reload` module resolution issue.

### 2.2 Configuration to Extend
- `config/settings.json` already has `hardware_label`, `execution_environment`, and `connection_type` fields added but not yet reflected in the dashboard UI select dropdowns (they are in the HTML).

---

## 3. Missing — What Must Be Added for v1.1

### 3.1 Task Definition System
- A new `Task` abstraction (YAML or JSON) to define:
  - Task type (markdown, python, java, unsolvable)
  - File artifacts to create
  - Validation command (markdownlint, pytest, gradlew/mvn)
  - Pass/fail criteria (exit code, specific output patterns)
  - Expected blockers (for unsolvable tasks)
- Storage: `tasks/` directory with individual task definition files.

### 3.2 Validator/Runner Infrastructure
- A `Validator` interface with implementations:
  - `MarkdownValidator` — runs `markdownlint`, parses output
  - `PythonValidator` — runs `pytest`, parses results
  - `JavaValidator` — runs build tool, parses results
  - `UnsolvableValidator` — checks for hallucination/drift detection
- Command execution framework (subprocess wrapper with timeout, output capture, exit code handling).

### 3.3 New Benchmark Engine Mode
- Extension of `run_benchmark()` or a new `run_task_benchmark()` function that:
  - Loads task definitions
  - Sends the task prompt to the LLM
  - Captures the LLM's response (files/code)
  - Invokes the appropriate validator
  - Records pass/fail + score alongside TTFT/tokens/sec
  - Detects unsolvable task drift (repeated attempts, hallucinated fixes)

### 3.4 New CSV Columns
- `score` — numeric score (0–100 or 0–1)
- `status` — pass/fail/skipped/drifted
- `task_id` — which task was run
- `validator` — which validator was used
- `validator_output` — raw validator output (optional, may be large)

### 3.5 Task Management API Endpoints
- `GET /api/tasks` — List available tasks
- `POST /api/tasks` — Create/define a task
- `POST /api/benchmark/run-tasks` — Run benchmark against task set

### 3.6 Dashboard Updates
- Task selection UI (checkboxes for which tasks to run)
- Score visualization (gauge/bar charts)
- Drift detection visualization for unsolvable tasks

---

## 4. Problems

### 4.1 Dead / Obsolete Code
- **`results.json` reference in `.gitignore`** (line 12) — The code explicitly says "Old JSON results.json left untouched" but this file does not exist and is not referenced anywhere in active code. It's a ghost reference.
- **`ResultsStore._format_value()` method** (line 115 of `results.py`) — Defined but never called. Dead method.
- **`connection_type` field in config** — Stored in CSV but the dashboard UI for it is only shown when "Self-hosted" is selected. The default config has `"connection_type": "Local network"` but this value is meaningless for "Local" execution environment. Minor confusion.

### 4.2 Inconsistencies
- **README vs. code — `GET /api/benchmark/results` schema:** README says it returns "All saved benchmark results" but the actual endpoint returns results grouped by `run_id` (it calls `get_grouped_results()` internally). The ungrouped endpoint does not exist as a separate route. This is a **documentation mismatch**.
- **README says "No telemetry"** but the dashboard calls `loadModels()` on init and `loadResults()` on init — these are local calls, not telemetry. This is fine. No actual issue.
- **`start_bench.bat` CD logic:** Line 52 does `cd /d "%PROJECT_DIR%"` but line 53 then uses the full path `"%PROJECT_DIR%\src\server_launcher.py"`. The CD is unnecessary since the launcher already handles `sys.path`. Minor redundancy.

### 4.3 Repository Cleanliness
- **Git state:** No commits yet. Repository is in pre-first-push state. `.gitignore` is present but nothing is tracked.
- **`benchmark_results.csv` exists** in `data/` — This is a generated data file. It is correctly listed in `.gitignore` but appears in `dir` output. If someone clones and runs benchmarks, this file will be created and should stay untracked.
- **No tests:** The repository has zero test files. No `tests/` directory, no test configuration.
- **No `__init__.py` files:** The `src/`, `config/`, `data/`, and `static/` directories lack `__init__.py` files. This works because the launcher uses `sys.path.insert()` and `uvicorn --reload` handles imports, but it's non-standard and could confuse IDEs.
- **Python version in README:** README says "Python 3.10+" but the code uses `list[dict]` type hints (line 14 of `benchmark.py`, line 51 of `results.py`) which require Python 3.9+. Python 3.10 is fine since `list[dict]` is valid from 3.9+.

### 4.4 Technical Debt
- **`dashboard.js` is 1353 lines of unminified vanilla JS** — Functional but unmaintainable long-term. No build step, no linting.
- **CSS is 469 lines** — No CSS variables, no theming system. Hard to maintain.
- **Hardcoded LM Studio URL in default config** — `"lm_studio_url": "http://localhost:1234"` — This is fine for the tool's purpose but could cause confusion if users run it against non-LM Studio servers.
- **No input validation for model key format** — The model key is passed directly to the LM Studio API without sanitization.
- **`temperature` default in config is `0` (integer) but the code expects float** — Line 7 of `settings.json` has `"temperature": 0` (int). The dashboard sends it as a float via `parseFloat()`, so this works in practice but is inconsistent.

---

## 5. Recommended v1.1 Architecture

### Guiding Principle: Smallest Clean Extension

```
bench_llm/
├── tasks/                          # NEW: Task definitions
│   ├── markdown_broken/            # Markdown task
│   │   ├── task.yaml               # Task metadata
│   │   └── broken_doc.md           # Deliberately broken document
│   ├── python_broken/              # Python task
│   │   ├── task.yaml
│   │   └── src/                    # Broken Python project
│   ├── java_broken/                # Java task
│   │   ├── task.yaml
│   │   └── src/                    # Broken Java project
│   └── unsolvable/                 # Unsolvable task
│       ├── task.yaml
│       └── prompt.txt
├── src/
│   ├── validators/                 # NEW: Validator abstractions
│   │   ├── __init__.py
│   │   ├── base.py                 # Validator interface
│   │   ├── markdown.py             # markdownlint validator
│   │   ├── python.py               # pytest validator
│   │   ├── java.py                 # build tool validator
│   │   └── unsolvable.py           # Drift detection validator
│   ├── task_runner.py              # NEW: Task execution engine
│   │                           # (reuses existing benchmark infrastructure)
│   ├── benchmark.py              # EXISTING: Keep as-is
│   ├── results.py                # EXISTING: Extend with new columns
│   ├── main.py                   # EXISTING: Add task endpoints
│   └── config_loader.py          # EXISTING: Keep as-is
├── config/
│   └── settings.json             # EXTEND: Add task-related config
├── data/
│   ├── prompts.json              # EXISTING: Keep
│   └── benchmark_results.csv     # EXTEND: New columns
├── static/
│   ├── index.html                # EXTEND: Add task selection UI
│   ├── dashboard.js              # EXTEND: Add task scoring charts
│   └── style.css                 # EXTEND: Add task-related styles
└── requirements.txt              # EXTEND: Add any new deps (markdownlint CLI?)
```

### Key Design Decisions

1. **Do NOT rewrite `benchmark.py`** — It works. Extend with a new `run_task_benchmark()` function that wraps the existing flow.
2. **Do NOT rewrite the dashboard** — Add task-related UI elements and charts alongside existing ones.
3. **Task definitions as YAML** — More readable than JSON for structured task specs. Use PyYAML (lightweight dep).
4. **Validators as subprocess runners** — Each validator runs a CLI tool (markdownlint, pytest, gradlew) and parses output.
5. **CSV schema extension** — Add columns: `task_id`, `score`, `status`, `validator`, `validator_output`. Do NOT remove existing columns.
6. **Unsolvable task detection** — Track response patterns: repeated identical fixes, hallucinated dependencies, failure to recognize impossibility.

### Minimal Dependencies Added
- `pyyaml` — For task definition files
- `subprocess32` (optional, for timeout) — Or use built-in `asyncio.create_subprocess_exec`

---

## 6. Recommended First Implementation Step

**Step 1: Create the task definition format and the markdown task.**

Before building validators or dashboard UI, establish the task definition schema and implement the first concrete task (markdown). This gives:

1. A concrete format to validate against
2. A reference implementation for the python and java tasks
3. The foundation for the task runner engine

### Specific actions:
1. Create `tasks/` directory with `markdown_broken/task.yaml` and `broken_doc.md`
2. Create `src/validators/base.py` with a `Validator` abstract base class
3. Create `src/validators/markdown.py` with `MarkdownValidator` implementation
4. Create `src/task_runner.py` with `run_task_benchmark()` function
5. Add `GET /api/tasks` and `POST /api/benchmark/run-tasks` endpoints to `main.py`
6. Extend `ResultsStore` with new CSV columns

**Do NOT touch:**
- `benchmark.py` (keep as-is)
- `results.py` core logic (only extend CSV_HEADERS and add_run)
- `config_loader.py` (only extend settings.json)
- Dashboard charts (add task scoring after the backend works)

---

## Summary Table

| Category | Count | Notes |
|----------|-------|-------|
| Working features | 5 | Benchmark, CSV, API, Dashboard, Prompts |
| Reusable infrastructure | 5 | ResultsStore, benchmark engine, config, prompts, server launcher |
| Must add (v1.1) | 6 | Task format, validators, task runner, new CSV columns, task API, dashboard updates |
| Dead code | 3 | `_format_value()`, `results.json` reference, unnecessary CD in batch file |
| Documentation mismatches | 1 | `/api/benchmark/results` grouping behavior |
| Repository issues | 4 | No commits, no tests, no `__init__.py`, generated CSV in repo |