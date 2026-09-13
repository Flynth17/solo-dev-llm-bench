"""Act 19.1 -- dedicated Standard Speed result page routing tests.

Covers the single-run Speed read model (src/v2_speed_read_model.py), the new API
route /api/speed/runs/{run_id}, the completed-status routing fix, the launcher
fallback correction, and that legacy /results + Workflow /v2/results are untouched.

No real benchmark run is required: read-model tests use in-memory rows and route
tests seed a temporary ResultsStore. A small group of real-run verification tests
skip gracefully when the persisted example run is absent from the environment.
"""

import tempfile
from pathlib import Path

import pytest

from src.results import ResultsStore
from src.main import app
from src.v2_speed_read_model import (
    STANDARD_SPEED_CANONICAL_POINTS,
    load_speed_run_by_id,
    SpeedReadModelIntegrityError,
    SpeedRunNotFoundError,
)
from src.v2_speed_suite_runner import _validate_speed_run_id

# ---------------------------------------------------------------------------
# Helpers: build persisted Standard Speed rows (in-memory or via a temp store).
# ---------------------------------------------------------------------------

POINT_LABEL = {8192: "8K", 16384: "16K", 32768: "32K"}


def _row(run_id: str, model_key: str = "ornith-1.5-35b-a3b", **over) -> dict:
    base = {
        "timestamp": "2026-08-14T12:00:00+00:00",
        "run_id": run_id,
        "model_key": model_key,
        "model_display_name": "Ornith 1.5 (35B)",
        "hardware_label": "RTX 5090",
        "execution_environment": "Local",
        "connection_type": "Local network",
        "iteration": 1,
        "cold_or_warm": "warm",
        "speed_point_status": "completed",
        "loaded_context": 262144,
        "model_max_context": 262144,
        "max_output_tokens": 512,
        "temperature": 0.5,
    }
    base.update(over)
    return base


def _point(run_id: str, target: int, inp: int, out: int,
           ttft: float, pre: float, gen: float, wall: float, **over) -> dict:
    d = _row(run_id,
             context_point=POINT_LABEL.get(target, str(target)),
             target_context_tokens=target,
             input_tokens=inp,
             output_tokens=out,
             ttft_seconds=ttft,
             prefill_tokens_per_second=pre,
             tokens_per_second=gen,
             wall_time_seconds=wall)
    d.update(over)
    return d


def standard_run(run_id: str = "speed-unit-0001") -> list[dict]:
    """The three-point Standard Speed contract in a deliberately non-canonical order."""
    return [
        _point(run_id, 32768, 22044, 63, 0.04, 551100.0, 176.68, 25.90),
        _point(run_id, 8192, 5561, 55, 0.04, 139025.0, 185.72, 8.07),
        _point(run_id, 16384, 11056, 53, 0.04, 276400.0, 185.88, 13.95),
    ]


# ---------------------------------------------------------------------------
# Fixture: TestClient over the real app with a temp ResultsStore seeded so that
# route-level tests never touch the durable data/benchmark_results.db file.
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded_speed():
    """Patch app_state.results_store with a temporary store holding one standard run."""
    from fastapi.testclient import TestClient
    client = TestClient(app)

    tmp = tempfile.mkdtemp()
    csv_path = Path(tmp) / "benchmark_results.csv"
    db_path = Path(tmp) / "benchmark_results.db"

    import src.app_state as app_state_module
    old_store = app_state_module.results_store
    store = ResultsStore(csv_path=csv_path, db_path=db_path)

    run_id = standard_run()
    for r in run_id:
        store.add_run(r)
    sid = "speed-unit-0001"

    try:
        app_state_module.results_store = store
        yield client, sid
    finally:
        app_state_module.results_store = old_store


# ---------------------------------------------------------------------------
# 1-12. Single-run Speed read model (direct, no route).
# ---------------------------------------------------------------------------

class TestSpeedReadModelDirect:

    def test_exact_known_run_loads_by_id(self):
        result = load_speed_run_by_id("speed-unit-0001", standard_run())
        assert result["run_id"] == "speed-unit-0001"
        assert result["model_identifier"] == "ornith-1.5-35b-a3b"
        assert len(result["points"]) == 3

    def test_unknown_run_raises_not_found(self):
        with pytest.raises(SpeedRunNotFoundError):
            load_speed_run_by_id("speed-nope", standard_run())

    def test_canonical_points_ordered_8_16_32(self):
        # Rows are seeded in reverse order; the read model must still return canonical.
        result = load_speed_run_by_id("speed-unit-0001", standard_run())
        assert [p["label"] for p in result["points"]] == ["8K", "16K", "32K"]
        assert [p["target_context_tokens"] for p in result["points"]] == \
            list(STANDARD_SPEED_CANONICAL_POINTS.keys())

    def test_actual_prompt_tokens_preserved(self):
        result = load_speed_run_by_id("speed-unit-0001", standard_run())
        assert [p["actual_prompt_tokens"] for p in result["points"]] == [5561, 11056, 22044]

    def test_ttft_preserved(self):
        result = load_speed_run_by_id("speed-unit-0001", standard_run())
        assert [p["ttft_seconds"] for p in result["points"]] == [0.04, 0.04, 0.04]

    def test_prefill_value_preserved_unchanged(self):
        # Prefill semantics are under review (Act 20); this Act must NOT recompute them.
        result = load_speed_run_by_id("speed-unit-0001", standard_run())
        assert [p["prefill_tokens_per_second"] for p in result["points"]] == \
            [139025.0, 276400.0, 551100.0]

    def test_generation_throughput_preserved(self):
        result = load_speed_run_by_id("speed-unit-0001", standard_run())
        assert [p["generation_tokens_per_second"] for p in result["points"]] == \
            [185.72, 185.88, 176.68]

    def test_completion_tokens_preserved(self):
        result = load_speed_run_by_id("speed-unit-0001", standard_run())
        assert [p["completion_tokens"] for p in result["points"]] == [55, 53, 63]

    def test_wall_time_preserved(self):
        result = load_speed_run_by_id("speed-unit-0001", standard_run())
        assert [p["wall_time_seconds"] for p in result["points"]] == [8.07, 13.95, 25.90]

    def test_identity_mismatch_rejected(self):
        mixed = [
            _point("speed-unit-0001", 8192, 5561, 55, 0.04, 139025.0, 185.72, 8.07, model_key="model-a"),
            _point("speed-unit-0001", 16384, 11056, 53, 0.04, 276400.0, 185.88, 13.95, model_key="model-b"),
            _point("speed-unit-0001", 32768, 22044, 63, 0.04, 551100.0, 176.68, 25.90),
        ]
        with pytest.raises(SpeedReadModelIntegrityError):
            load_speed_run_by_id("speed-unit-0001", mixed)

    def test_duplicate_canonical_point_rejected(self):
        dup = [
            _point("speed-unit-0001", 8192, 5561, 55, 0.04, 139025.0, 185.72, 8.07),
            _point("speed-unit-0001", 8192, 9999, 1, 0.04, 1.0, 1.0, 1.0),
            _point("speed-unit-0001", 16384, 11056, 53, 0.04, 276400.0, 185.88, 13.95),
            _point("speed-unit-0001", 32768, 22044, 63, 0.04, 551100.0, 176.68, 25.90),
        ]
        with pytest.raises(SpeedReadModelIntegrityError):
            load_speed_run_by_id("speed-unit-0001", dup)

    def test_out_of_contract_target_rejected(self):
        off = [_point("speed-unit-0001", 8192, 5561, 55, 0.04, 139025.0, 185.72, 8.07),
               _point("speed-unit-0001", 49152, 44088, 99, 0.04, 999900.0, 100.0, 40.0)]
        with pytest.raises(SpeedReadModelIntegrityError):
            load_speed_run_by_id("speed-unit-0001", off)

    def test_unsupported_point_representable(self):
        # A stored-but-unsupported point must surface, not crash the read.
        rows = standard_run()
        for r in rows:
            if r["target_context_tokens"] == 32768:
                r["speed_point_status"] = "unsupported"
        result = load_speed_run_by_id("speed-unit-0001", rows)
        assert result["status"] == "partial"
        unsupported = [p for p in result["points"] if p["target_context_tokens"] == 32768][0]
        assert unsupported["status"] == "unsupported"


# ---------------------------------------------------------------------------
# Route / API behaviour (items 2, 10/11 via HTTP, 13-16).
# ---------------------------------------------------------------------------

class TestSpeedResultRouting:

    def test_api_unknown_run_returns_404(self, seeded_speed):
        client, _ = seeded_speed
        resp = client.get("/api/speed/runs/speed-does-not-exist")
        assert resp.status_code == 404

    def test_invalid_run_id_rejected_by_validator(self):
        # Path-traversal guard: unsafe ids raise before any filesystem interaction.
        for bad in ("", "speed/evil", "\\evil", "a\\b"):
            with pytest.raises(ValueError):
                _validate_speed_run_id(bad)

    def test_api_invalid_run_id_never_returns_live_payload(self, seeded_speed):
        # An unsafe / encoded id must never return a 200 result payload.
        client, _ = seeded_speed
        resp = client.get("/api/speed/runs/%2Fetc%2Fpasswd")
        assert resp.status_code != 200

    def test_api_standard_run_returns_three_canonical_points(self, seeded_speed):
        client, sid = seeded_speed
        resp = client.get(f"/api/speed/runs/{sid}")
        assert resp.status_code == 200
        body = resp.json()
        assert [p["label"] for p in body["points"]] == ["8K", "16K", "32K"]
        assert body["configuration"]["loaded_context"] == 262144

    def test_completed_status_routes_to_dedicated_page(self, seeded_speed):
        from src.v2_speed_suite_runner import speed_status
        client, sid = seeded_speed
        status_code, body = speed_status(sid)
        assert status_code == 200
        assert body["status"] == "completed"
        assert body["result_url"] == f"/speed/results/{sid}"

    def test_launcher_fallback_uses_speed_route_not_v2(self):
        path = Path(__file__).resolve().parent.parent / "static" / "dashboard.js"
        src = path.read_text(encoding="utf-8")
        # Speed must no longer fall back to the Workflow result route.
        assert '("/speed/results/" + runId)' in src
        # ...while the Workflow fallback correctly still points at /v2/results.
        assert '("/v2/results/" + runId)' in src

    def test_legacy_results_page_still_works(self, seeded_speed):
        client, _ = seeded_speed
        resp = client.get("/results")
        assert resp.status_code == 200
        assert "text/html" in (resp.headers.get("content-type", "") or "")

    def test_workflow_v2_results_page_unchanged(self, seeded_speed):
        client, _ = seeded_speed
        # The Workflow result page serves for any run id; routing is unchanged.
        resp = client.get("/v2/results/some-workflow-run")
        assert resp.status_code == 200
        assert "text/html" in (resp.headers.get("content-type", "") or "")


class TestDedicatedSpeedPage:

    def test_dedicated_speed_page_serves_html(self, seeded_speed):
        client, sid = seeded_speed
        resp = client.get(f"/speed/results/{sid}")
        assert resp.status_code == 200
        assert "text/html" in (resp.headers.get("content-type", "") or "")
        assert "sr-table" in resp.text
        assert "SPEED RESULT" in resp.text


# ---------------------------------------------------------------------------
# Real-run verification against the durable store (skips if example absent).
# ---------------------------------------------------------------------------

def _real_store_has_run(run_id: str) -> bool:
    import src.app_state as app_state_module
    return any((r.get("run_id") or "") == run_id for r in app_state_module.results_store.get_all())


@pytest.mark.skipif(
    not _real_store_has_run("speed-c32287906073"),
    reason="persisted example run speed-c32287906073 is not present in this environment",
)
class TestRealRunVerification:

    def test_real_run_api_returns_single_speed_run(self):
        from fastapi.testclient import TestClient
        client = TestClient(app)
        resp = client.get("/api/speed/runs/speed-c32287906073")
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == "speed-c32287906073"
        assert [p["label"] for p in body["points"]] == ["8K", "16K", "32K"]
        assert [p["prefill_tokens_per_second"] for p in body["points"]] == \
            [139025.0, 276400.0, 551100.0]

    def test_real_run_dedicated_page_returns_200(self):
        from fastapi.testclient import TestClient
        client = TestClient(app)
        resp = client.get("/speed/results/speed-c32287906073")
        assert resp.status_code == 200
