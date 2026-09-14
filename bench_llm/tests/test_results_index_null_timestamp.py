"""Regression guard: GET /api/results must not crash when a stored Speed run has no timestamp.

Standalone Speed runs persist ``timestamp = None`` (only legacy quality rows carry an ISO
timestamp). The Standard Speed history is sorted newest-first with
``key=lambda s: s.get("timestamp") or ""`` -- and a NULL/None value would make Python compare
``None < None`` and raise ``TypeError``, which FastAPI surfaces as a 500 and breaks the entire
/results page (and thus "View Result") for everyone.

This seeds one row with ``timestamp=None`` (Speed-shaped) alongside timestamped Speed rows and
asserts the endpoint returns 200, includes the untimed row in the Standard Speed history, and
does not crash. Mirrors the temp-store + app_state.patch convention in tests/test_delete.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(__import__("src.main", fromlist=["app"]).app)


@pytest.fixture
def seeded_store(client):
    import src.app_state as app_state_module
    from src.results import ResultsStore

    tmp_dir = tempfile.mkdtemp()
    csv_path = Path(tmp_dir) / "benchmark_results.csv"
    db_path = Path(tmp_dir) / "benchmark_results.db"

    old_store = app_state_module.results_store
    new_store = ResultsStore(csv_path=csv_path, db_path=db_path)
    app_state_module.results_store = new_store
    try:
        yield new_store
    finally:
        app_state_module.results_store = old_store


def _speed_row(run_id: str, timestamp=None) -> dict:
    """A standalone Speed run row -- note timestamp is None, exactly like the real runner."""
    return {
        "timestamp": timestamp,
        "run_id": run_id,
        "model_key": "ornith-1.5-35b-a3b",
        "model_display_name": "Ornith 1.5 35B A3B",
        "hardware_label": "LM Studio local",
        "execution_environment": "Local",
        "connection_type": "",
        "iteration": 5,
        "cold_or_warm": "warm",
        "tokens_per_second": 185.72,
        "ttft_seconds": 0.04,
        "input_tokens": 5561,
        "output_tokens": 55,
        "prefill_tokens_per_second": 139025.0,
        "wall_time_seconds": 8.07,
        "max_output_tokens": 512,
        "temperature": 0.0,
        "target_context_tokens": 8192,
        "context_point": "8K",
        "speed_point_status": "completed",
    }


def test_results_index_does_not_crash_on_null_timestamp(client, seeded_store):
    seeded_store.add_run(_speed_row("speed-reg-1"))

    resp = client.get("/api/results")

    # Previously: 500 Internal Server Error (TypeError on None < None in the sort key).
    assert resp.status_code == 200
    speed_runs = {r["run_id"]: r for r in resp.json().get("speed_runs", [])}
    assert "speed-reg-1" in speed_runs
    # The untimed Speed run is still surfaced as a Standard Speed history entry.
    entry = speed_runs["speed-reg-1"]
    assert entry.get("type") == "standard_speed"
    points = entry.get("points") or []
    assert points and points[0].get("label") == "8K"


def test_results_index_still_sorts_newest_first(client, seeded_store):
    # Two timed Speed rows (different dates) plus one untimed (None) Speed run.
    seeded_store.add_run(_speed_row("fast-new", timestamp="2026-03-01T00:00:00+00:00"))
    seeded_store.add_run(_speed_row("slow-old", timestamp="2026-01-01T00:00:00+00:00"))
    seeded_store.add_run(_speed_row("none-ts"))  # timestamp=None -> sorts last

    resp = client.get("/api/results")
    assert resp.status_code == 200
    ordered = [r["run_id"] for r in resp.json().get("speed_runs", [])]
    # Newest-first: the newer timestamp precedes the older, and the untimed row lands last.
    assert ordered.index("fast-new") < ordered.index("slow-old")
    assert ordered.index("slow-old") < ordered.index("none-ts")
