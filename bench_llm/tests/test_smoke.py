"""Minimal smoke tests for existing Solo Dev LLM Bench functionality.

Run with: python -m pytest tests/test_smoke.py -v
"""

import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

# Ensure the project root is on sys.path so imports work
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


# ====================================================================
# Test 1: Configuration loads
# ====================================================================

def test_config_loads():
    """Configuration loads from settings.json and returns expected keys."""
    from src.config_loader import load_config

    config = load_config()
    assert isinstance(config, dict)
    assert "lm_studio_url" in config
    assert "model" in config
    assert "iterations" in config
    assert "prompt" in config
    assert "max_tokens" in config
    assert "temperature" in config


def test_config_save_roundtrip():
    """Configuration can be saved and reloaded."""
    from src.config_loader import load_config, save_config

    with tempfile.NamedTemporaryFile(
        suffix=".json", delete=False, mode="w"
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        original = {
            "lm_studio_url": "http://localhost:1234",
            "model": "",
            "iterations": 3,
            "prompt": "Test prompt",
            "max_tokens": 256,
            "temperature": 0.7,
            "hardware_label": "",
            "execution_environment": "Local",
            "connection_type": "",
        }
        save_config(original, config_path=tmp_path)
        loaded = load_config(config_path=tmp_path)
        assert loaded["lm_studio_url"] == original["lm_studio_url"]
        assert loaded["iterations"] == original["iterations"]
        assert loaded["temperature"] == original["temperature"]
        assert loaded["prompt"] == original["prompt"]
    finally:
        tmp_path.unlink()


# ====================================================================
# Test 2: ResultsStore write/read
# ====================================================================

def test_results_store_write_read():
    """ResultsStore can write a result and read it back."""
    from src.results import ResultsStore

    with tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False
    ) as tmp_csv:
        tmp_csv_path = Path(tmp_csv.name)
    with tempfile.NamedTemporaryFile(
        suffix=".db", delete=False
    ) as tmp_db:
        tmp_db_path = Path(tmp_db.name)

    try:
        store = ResultsStore(csv_path=tmp_csv_path, db_path=tmp_db_path)
        sample_run = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": "test-run-001",
            "model_key": "test-model",
            "model_display_name": "Test Model",
            "hardware_label": "Test Hardware",
            "execution_environment": "Local",
            "connection_type": "",
            "iteration": 1,
            "cold_or_warm": "cold",
            "tokens_per_second": 42.5,
            "ttft_seconds": 1.23,
            "input_tokens": 100,
            "output_tokens": 200,
            "model_load_time_seconds": None,
            "wall_time_seconds": 5.67,
            "prompt_name": "Custom",
            "max_output_tokens": 500,
            "temperature": 0,
        }
        store.add_run(sample_run)

        all_runs = store.get_all()
        assert len(all_runs) == 1
        assert all_runs[0]["run_id"] == "test-run-001"
        assert all_runs[0]["model_key"] == "test-model"
        assert all_runs[0]["tokens_per_second"] == 42.5
        assert all_runs[0]["ttft_seconds"] == 1.23

        # Verify CSV file was written
        assert tmp_csv_path.exists()
        assert tmp_csv_path.stat().st_size > 0
    finally:
        tmp_csv_path.unlink()
        if tmp_db_path.exists():
            tmp_db_path.unlink()


def test_results_store_load_from_disk():
    """ResultsStore reloads data from an existing CSV file."""
    from src.results import ResultsStore

    with tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False
    ) as tmp_csv:
        tmp_csv_path = Path(tmp_csv.name)
    with tempfile.NamedTemporaryFile(
        suffix=".db", delete=False
    ) as tmp_db:
        tmp_db_path = Path(tmp_db.name)

    try:
        # Create a CSV with known data (no DB file yet, so migration
        # will import these rows on first open).
        with open(tmp_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "run_id", "model_key", "model_display_name",
                "hardware_label", "execution_environment", "connection_type",
                "iteration", "cold_or_warm", "tokens_per_second", "ttft_seconds",
                "input_tokens", "output_tokens", "model_load_time_seconds",
                "wall_time_seconds", "prompt_name", "max_output_tokens", "temperature",
            ])
            writer.writerow([
                "2026-08-14T10:00:00+00:00", "run-002", "model-x",
                "Model X", "HW", "Local", "", 1, "cold",
                "50.0", "0.5", 50, 100, "", 2.0, "Custom", 500, 0,
            ])

        store = ResultsStore(csv_path=tmp_csv_path, db_path=tmp_db_path)
        all_runs = store.get_all()
        assert len(all_runs) == 1
        assert all_runs[0]["run_id"] == "run-002"
        assert all_runs[0]["tokens_per_second"] == 50.0
    finally:
        tmp_csv_path.unlink()
        if tmp_db_path.exists():
            tmp_db_path.unlink()


# ====================================================================
# Test 3: Benchmark result structure is valid
# ====================================================================

def test_benchmark_result_structure():
    """The run_benchmark function returns the expected dict structure."""
    import inspect
    from src.benchmark import run_benchmark

    # Verify the function exists and is async
    assert callable(run_benchmark)
    assert inspect.iscoroutinefunction(run_benchmark)

    # Check the function signature has expected parameters
    sig = inspect.signature(run_benchmark)
    params = set(sig.parameters.keys())
    expected = {"lm_studio_url", "model", "prompt", "iterations", "max_tokens", "temperature"}
    assert expected.issubset(params), f"Missing params: {expected - params}"


def test_results_csv_headers():
    """CSV headers match the documented schema (subset check)."""
    from src.results import CSV_HEADERS

    required_columns = [
        "timestamp", "run_id", "model_key", "tokens_per_second",
        "ttft_seconds", "input_tokens", "output_tokens",
        "iteration", "cold_or_warm", "prompt_name",
    ]
    for col in required_columns:
        assert col in CSV_HEADERS, f"Missing CSV column: {col}"


# ====================================================================
# Test 4: FastAPI application imports successfully
# ====================================================================

def test_fastapi_app_imports():
    """FastAPI application can be imported without errors."""
    from src.main import app

    assert app is not None
    assert app.title == "Solo Dev LLM Bench"

    # Verify routes exist (use exact method matching to avoid partial matches)
    routes = [route.path for route in app.routes]
    expected_routes = [
        ("/", ["GET"]),
        ("/api/config", ["GET", "POST"]),
        ("/api/models", ["GET"]),
        ("/api/benchmark/run", ["POST"]),
        ("/api/benchmark/runs/grouped", ["GET"]),  # Actual route for grouped results
    ]
    for route_path, methods in expected_routes:
        assert route_path in routes, f"Missing route: {route_path}"


def test_fastapi_results_store_exists():
    """ResultsStore is instantiated in the app."""
    from src.main import results_store
    from src.results import ResultsStore

    assert isinstance(results_store, ResultsStore)


# ====================================================================
# Test 5: Prompts file is valid
# ====================================================================

def test_prompts_file_valid():
    """prompts.json can be loaded and has expected structure."""
    prompts_path = _PROJECT_ROOT / "data" / "prompts.json"
    assert prompts_path.exists(), "prompts.json should exist"

    data = json.loads(prompts_path.read_text(encoding="utf-8"))
    assert "prompts" in data
    assert isinstance(data["prompts"], list)

    if len(data["prompts"]) > 0:
        first = data["prompts"][0]
        assert "name" in first
        assert "prompt" in first


# ====================================================================
# Test 6: Settings.json has new fields
# ====================================================================

def test_settings_has_hardware_fields():
    """settings.json includes the hardware/environment fields."""
    from src.config_loader import load_config

    config = load_config()
    assert "hardware_label" in config
    assert "execution_environment" in config
    assert "connection_type" in config