# Solo Dev LLM Bench

**Solo Dev LLM Bench measures performance trade-offs when switching LLMs, hardware, or execution environments.**

This project measures performance, **not** intelligence or model quality.

## What It Is

A lightweight, Windows-native dashboard for solo developers to benchmark locally hosted LLMs via LM Studio. It answers practical questions like:

- If I replace my current model, how much slower/faster will it be?
- What happens to TTFT (time to first token)?
- What happens to sustained tokens/sec?
- How different are cold and warm runs?
- If I move from my desktop to a laptop, is the experience still usable?
- If I access my own AI machine remotely, what is the practical performance trade-off?

## What It Is NOT

- **Not a model quality evaluator.** It does not score, rank, or recommend models.
- **Not a cloud service.** Everything runs locally on your machine.
- **Not a hardware purchasing tool.** It measures performance; you decide what to do with that data.
- **Not an automated decision engine.** It reports measurements. You make the decision.

## Requirements

- **Windows 10/11**
- **Python 3.10+** ([python.org](https://www.python.org/downloads/))
- **LM Studio** running with at least one LLM loaded ([lmstudio.ai](https://lmstudio.ai/))

No administrator privileges required.

## Installation

```bash
cd bench_llm
pip install -r requirements.txt
```

## One-Click Startup (Windows)

Double-click:

```
start_bench.bat
```

This will:
1. Open your browser to `http://localhost:8000`
2. Start the Solo Dev LLM Bench server

## Manual Startup

```bash
cd bench_llm
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open `http://localhost:8000` in your browser.

### Exposing to LAN (optional)

Advanced users who want to access the dashboard from another machine on their local network can start the server with:

```bash
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

> **Warning:** This exposes the dashboard to your local network. Only do this on trusted networks.

## Usage

1. **LM Studio URL** — Confirm the default `http://localhost:1234` or enter your LM Studio server address.
2. **Refresh Models** — Click the refresh button (🔄) to populate the model dropdown from `GET /api/v1/models`.
3. **Select a Model** — Choose any LLM from the dropdown.
4. **Execution Environment** — Select whether the benchmark ran on Local, Self-hosted, or Cloud hardware.
5. **Hardware Label** — (Optional) Name your machine (e.g., "RTX 5090 Desktop", "Work Laptop").
6. **Benchmark Prompt** — Edit the default prompt if desired.
7. **Iterations / Max Tokens / Temperature** — Adjust as needed (defaults: 5 iterations, 500 max tokens, temperature 0).
8. **Run Benchmark** — Click to start. Results appear immediately on completion.

## CSV Results Location

Benchmark results are stored in:

```
data/benchmark_results.csv
```

### CSV Schema

| Column                  | Description                                      |
|------------------------|--------------------------------------------------|
| `timestamp`            | ISO-8601 UTC timestamp of the run                |
| `run_id`               | UUID identifying this benchmark run (shared across iterations) |
| `model_key`            | Model identifier from LM Studio                  |
| `model_display_name`   | Human-readable model name                        |
| `hardware_label`       | User-provided hardware label (optional)          |
| `execution_environment`| Local / Self-hosted / Cloud                      |
| `connection_type`      | Local network / Remote connection (if Self-hosted) |
| `iteration`            | Iteration number (1-based)                       |
| `cold_or_warm`         | cold (iteration 1) / warm (iterations 2+)        |
| `tokens_per_second`    | Tokens generated per second                      |
| `ttft_seconds`         | Time to first token (seconds)                    |
| `input_tokens`         | Number of input tokens                           |
| `output_tokens`        | Number of output tokens                          |
| `model_load_time_seconds` | Model load time from LM Studio (if available) |
| `wall_time_seconds`    | Wall-clock duration of the request               |
| `prompt_name`          | Identifier or name of the prompt used            |
| `max_output_tokens`    | Maximum output tokens setting                    |
| `temperature`          | Temperature setting used                         |

Results can be imported into:
- Excel
- SQLite / PostgreSQL
- pandas / Power BI / other analytics tools

## Benchmark Methodology

### Cold vs Warm Runs

- **Cold run (Iteration 1):** The first run after the model is loaded in LM Studio. Includes model loading overhead and cold cache effects.
- **Warm runs (Iterations 2+):** Subsequent runs benefit from cached computations and loaded model weights.

> **Note:** This classification is based on iteration order, not an independent cache verification. It is a practical heuristic, not a scientific guarantee.

### Metrics

- **TTFT (Time to First Token):** How long you wait before seeing the first token. Lower is better for perceived responsiveness.
- **Tokens/sec:** Sustained generation speed. Higher is better for throughput.
- **Wall time:** Total request duration.

## Privacy & Security

Solo Dev LLM Bench is designed as a **local-only utility**:

- **No telemetry.** Nothing is collected or uploaded anywhere.
- **No network scanning.** Execution environment labels are purely user-provided.
- **No automatic connection detection.** The tool does not inspect your network, test connectivity, or detect your connection type.
- **No accounts required.**
- **No API keys logged.** Model identifiers are stored; API keys are never sent or stored.
- **Benchmark data stays local.** All results remain in your project directory unless you move them yourself.

> **Note:** Cloud endpoints you benchmark against may themselves send data to the cloud provider if you later configure such functionality. Solo Dev LLM Bench itself does not provide or require a cloud service.

## Limitations

- **Windows-only.** Not tested on macOS or Linux.
- **Requires LM Studio.** Benchmarks are run through LM Studio's native API.
- **Single-model benchmarks.** Does not compare multiple models simultaneously.
- **No Docker or installer.** This is a simple, portable tool.
- **No automated hardware recommendations.** You decide what to buy or change.
- **No scientific cache control.** Cold/warm classification is heuristic.

## Configuration

Edit `config/settings.json` directly or update settings via the dashboard.

```json
{
    "lm_studio_url": "http://localhost:1234",
    "model": "",
    "iterations": 5,
    "prompt": "Write a short story about a robot learning to feel emotions.",
    "max_tokens": 500,
    "temperature": 0
}
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard page |
| GET | `/api/config` | Current configuration |
| POST | `/api/config` | Update configuration |
| GET | `/api/models` | List LLM models from LM Studio |
| POST | `/api/benchmark/run` | Run benchmark |
| GET | `/api/benchmark/results` | Saved benchmark results grouped by run_id |

## License

MIT License — you may use, modify, redistribute, and fork this project freely.

See `LICENSE` for the full text.

## Attribution

This project may be appreciated with a reference back to its origin, but attribution is not required beyond what the MIT License already specifies.

---

**Solo Dev LLM Bench** — Measures performance trade-offs. You decide what to do with that data.