"""Act 22 -- Standard Speed history grouping by run.

Validates the backend normalization contract that powers the /results history page:

* GET /api/results returns a backward-compatible payload -- the original ``results``
  array (every row, newest-first) is preserved UNCHANGED so legacy/custom runs keep
  rendering on their existing path, PLUS a new normalized ``speed_runs`` list of
  Standard Speed run summaries built by reusing load_speed_run_by_id().

The dedicated /speed/results/{run_id} page stays authoritative for per-run inspection;
the combined history never blends 8K/16K/32K into one misleading run-wide tok/s and never
re-implements Speed semantics client-side.

No benchmark mechanics, calibration, cache-busting, TTFT/prefill semantics, streaming,
speed_samples schema or Context suite are touched here.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

# --- fixture: temp-backed store patched into app_state, mirroring test_delete.py / ...null_timestamp


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(__import__("src.main", fromlist=["app"]).app)


@pytest.fixture
def history_store(client):
    """A fresh ResultsStore seeded with legacy/custom + Standard Speed runs.

    Seed layout (deliberately messy to prove normalization is robust, not order-dependent):

      * ``legacy-run``        : ordinary benchmark run -- no canonical context point.
                                MUST stay only in the flat ``results`` array.
      * ``speed-aaa`` (v2)     : Standard Speed with points inserted OUT of canonical
                                order (32K, 8K, 16K) -> normalized order must be 8K,16K,32K.
      * ``speed-bbb`` (v2)     : another Standard Speed run (single point) -> different
                                run_id must never interleave with speed-aaa's points.
      * ``speed-legacy``       : Standard Speed shape but NO metric version (pre-Act-20
                                legacy row) -> normalized as legacy + prefill warning,
                                values preserved verbatim.
    """
    import src.app_state as app_state_module
    from src.results import ResultsStore

    tmp_dir = tempfile.mkdtemp()
    csv_path = Path(tmp_dir) / "benchmark_results.csv"
    db_path = Path(tmp_dir) / "benchmark_results.db"

    old_store = app_state_module.results_store
    new_store = ResultsStore(csv_path=csv_path, db_path=db_path)
    app_state_module.results_store = new_store
    try:
        _seed(new_store)
        yield new_store
    finally:
        app_state_module.results_store = old_store


def _row(**kw):
    base = {
        "timestamp": "2026-05-01T00:00:00+00:00",
        "run_id": "",
        "model_key": "ornith-1.5-35b-a3b",
        "model_display_name": "Ornith 1.5 35B A3B",
        "hardware_label": "LM Studio local",
        "execution_environment": "Local",
        "connection_type": "",
    }
    base.update(kw)
    return base


def _seed(store):
    # --- legacy/custom run (no canonical context point); only flat results path
    store.add_run(_row(
        run_id="legacy-run", timestamp="2026-04-15T00:00:00+00:00",
        context_point="", target_context_tokens=None,
        iteration=1, cold_or_warm="warm", tokens_per_second=42.0, ttft_seconds=0.3,
        input_tokens=1200, output_tokens=512, prefill_tokens_per_second=8000.0,
        wall_time_seconds=6.0, max_output_tokens=512, temperature=0.7,
    ))

    # --- Standard Speed run speed-aaa : points inserted NON-canonically (32K,8K,16K)
    for point, insertion in (
        ("32K", 32768),
        ("8K", 8192),
        ("16K", 16384),
    ):
        store.add_run(_row(
            run_id="speed-aaa", context_point=point, target_context_tokens=insertion,
            iteration=1, cold_or_warm="cold", tokens_per_second=100.0, ttft_seconds=0.9,
            input_tokens=8150 if insertion == 8192 else (16300 if insertion == 16384 else 32600),
            output_tokens=512, prefill_tokens_per_second=9000.0,
            wall_time_seconds=8.0, max_output_tokens=512, temperature=0.0,
            speed_point_status="completed", speed_metric_version=2,
        ))

    # --- Standard Speed run speed-bbb : single 8K point (different run_id)
    store.add_run(_row(
        run_id="speed-bbb", context_point="8K", target_context_tokens=8192,
        iteration=1, cold_or_warm="cold", tokens_per_second=210.5, ttft_seconds=0.95,
        input_tokens=8160, output_tokens=512, prefill_tokens_per_second=9200.0,
        wall_time_seconds=7.6, max_output_tokens=512, temperature=0.0,
        speed_point_status="completed", speed_metric_version=2,
    ))

    # --- Standard Speed run 'legacy' shape : canonical points but NO metric version
    store.add_run(_row(
        run_id="speed-legacy", context_point="8K", target_context_tokens=8192,
        iteration=1, cold_or_warm="cold", tokens_per_second=185.72, ttft_seconds=0.04,
        input_tokens=5561, output_tokens=55, prefill_tokens_per_second=139025.0,
        wall_time_seconds=8.07, max_output_tokens=512, temperature=0.0,
        speed_point_status="completed",  # <-- no speed_metric_version key at all (legacy)
    ))


# ---------------------------------------------------------------------------
# Payload shape / backward compatibility
# ---------------------------------------------------------------------------

def test_payload_is_backward_compatible(history_store):
    """`results` array is preserved unchanged; `speed_runs` is an ADDITIVE new key."""
    from src.routes.results import get_past_results
    payload = asyncio_run(get_past_results())

    assert set(payload.keys()) == {"results", "speed_runs"}
    # Every seeded run_id still appears in the flat results array (legacy path intact).
    result_run_ids = {r["run_id"] for r in payload["results"]}
    for rid in ("legacy-run", "speed-aaa", "speed-bbb", "speed-legacy"):
        assert rid in result_run_ids, f"flat results lost run {rid}"


def test_legacy_custom_run_excluded_from_speed_runs(history_store):
    from src.routes.results import get_past_results
    payload = asyncio_run(get_past_results())
    speed_ids = {s["run_id"] for s in payload["speed_runs"]}
    assert "legacy-run" not in speed_ids          # ordinary benchmark run -> legacy path only
    assert {"speed-aaa", "speed-bbb", "speed-legacy"} <= speed_ids


# ---------------------------------------------------------------------------
# Run grouping: two different run_ids are separate, no interleaving
# ---------------------------------------------------------------------------

def test_two_speed_runs_render_as_separate_groups(history_store):
    from src.routes.results import get_past_results
    payload = asyncio_run(get_past_results())
    by_id = {s["run_id"]: s for s in payload["speed_runs"]}
    assert set(by_id) == {"speed-aaa", "speed-bbb", "speed-legacy"}
    # Each entry is its own group keyed only by run_id.
    for s in payload["speed_runs"]:
        assert s["type"] == "standard_speed"


def test_points_never_interleave_between_runs(history_store):
    from src.routes.results import get_past_results
    payload = asyncio_run(get_past_results())
    by_id = {s["run_id"]: s for s in payload["speed_runs"]}

    aaa_targets = {p["target_context_tokens"] for p in by_id["speed-aaa"]["points"]}
    bbb_targets = {p["target_context_tokens"] for p in by_id["speed-bbb"]["points"]}
    # Each run's point set is self-contained (grouped by run_id, never flattened across runs).
    assert aaa_targets == {8192, 16384, 32768}   # speed-aaa owns all three canonical points
    assert bbb_targets == {8192}                  # speed-bbb owns only its single point
    # Point counts match the expected per-run partition (no shared/collapsed entries).
    assert len(by_id["speed-aaa"]["points"]) == 3
    assert len(by_id["speed-bbb"]["points"]) == 1


def test_each_run_entries_carry_only_their_own_points(history_store):
    from src.routes.results import get_past_results
    payload = asyncio_run(get_past_results())
    by_id = {s["run_id"]: s for s in payload["speed_runs"]}
    # speed-bbb (single 8K point) must not contain a 16K/32K label belonging to speed-aaa.
    assert [p["label"] for p in by_id["speed-bbb"]["points"]] == ["8K"]


# ---------------------------------------------------------------------------
# Canonical ordering: 8K -> 16K -> 32K regardless of insertion order
# ---------------------------------------------------------------------------

def test_canonical_point_order_8K_16K_32K(history_store):
    from src.routes.results import get_past_results
    payload = asyncio_run(get_past_results())
    by_id = {s["run_id"]: s for s in payload["speed_runs"]}
    # speed-aaa was seeded 32K,8K,16K; normalization must reorder to canonical.
    assert [p["label"] for p in by_id["speed-aaa"]["points"]] == ["8K", "16K", "32K"]


# ---------------------------------------------------------------------------
# Metric version + legacy handling (real pipeline: v2 non-legacy, null legacy)
# ---------------------------------------------------------------------------

def test_v2_run_is_non_legacy(history_store):
    from src.routes.results import get_past_results
    payload = asyncio_run(get_past_results())
    by_id = {s["run_id"]: s for s in payload["speed_runs"]}
    aaa = by_id["speed-aaa"]
    assert aaa["metric_version"] == 2
    assert aaa["legacy_prefill_warning"] is False


def test_legacy_speed_run_carries_warning_and_preserves_values(history_store):
    from src.routes.results import get_past_results
    payload = asyncio_run(get_past_results())
    by_id = {s["run_id"]: s for s in payload["speed_runs"]}
    legacy = by_id["speed-legacy"]
    # Never fabricate a version historical rows never had -> null/None reads as legacy.
    assert legacy["metric_version"] is None
    assert legacy["legacy_prefill_warning"] is True
    # Measurements preserved VERBATIM -- not rewritten, not coerced to zero.
    pt = legacy["points"][0]
    assert pt["actual_prompt_tokens"] == 5561
    assert pt["ttft_seconds"] == 0.04
    assert pt["prefill_tokens_per_second"] == 139025.0


# ---------------------------------------------------------------------------
# No blended run-wide tok/s; no per-second fabrication; no speed_samples schema
# ---------------------------------------------------------------------------

_ALLOWED_SPEED_KEYS = {
    "run_id", "type", "model_identifier", "model_display_name",
    "hardware_label", "execution_environment", "connection_type",
    "timestamp", "metric_version", "legacy_prefill_warning", "points",
}
_ALLOWED_POINT_KEYS = {
    "label", "target_context_tokens", "actual_prompt_tokens", "ttft_seconds",
    "prefill_tokens_per_second", "generation_tokens_per_second",
    "completion_tokens", "wall_time_seconds",
}


def test_no_run_wide_blended_tps_and_no_top_level_aggregate(history_store):
    from src.routes.results import get_past_results
    payload = asyncio_run(get_past_results())
    for s in payload["speed_runs"]:
        # Point-based only -- no blended 8K+16K+32K average anywhere on the entry.
        for bad in ("avg_tokens_per_second", "min_tokens_per_second",
                    "max_tokens_per_second", "blended"):
            assert bad not in s, f"{bad} must never appear on a Standard Speed history card"
        # Points carry per-point generation rate (separate operating points).
        for p in s["points"]:
            assert isinstance(p.get("generation_tokens_per_second"), (int, float))


def test_no_per_second_timeline_or_speed_samples_schema(history_store):
    from src.routes.results import get_past_results
    payload = asyncio_run(get_past_results())
    # No fabricated per-second TIMELINE/SERIES data anywhere in the speed history.
    # (Legitimate per-point metrics legitimately end in ``_seconds`` / ``per_second`` and are kept.)
    blob = json.dumps(payload["speed_runs"])
    for forbidden in ("samples", "timeline", "per_second_series", "generated_samples"):
        assert forbidden not in blob.lower()
    # speed_samples schema explicitly absent (Act 22 forbids it).
    assert "speed_samples" not in blob.lower()


def test_speed_run_entry_keys_are_bounded(history_store):
    from src.routes.results import get_past_results
    payload = asyncio_run(get_past_results())
    for s in payload["speed_runs"]:
        assert set(s.keys()) <= _ALLOWED_SPEED_KEYS
        for p in s["points"]:
            assert set(p.keys()) <= _ALLOWED_POINT_KEYS


# ---------------------------------------------------------------------------
# Dedicated result link target is /speed/results/{run_id} (asserted statically)
# ---------------------------------------------------------------------------

def test_frontend_links_to_dedicated_speed_page_not_v2():
    static_js = Path(__file__).parent.parent / "static" / "results.js"
    text = static_js.read_text(encoding="utf-8")
    # History links to the dedicated single-run inspection page.
    assert "/speed/results/" in text
    # Never sends users to the legacy v2 run path from history grouping.
    assert "/v2/results/" not in text


def test_frontend_renders_point_based_speed_cards_not_blended():
    """The history page builds Standard Speed cards (point-based), never a blended aggregate."""
    static_js = Path(__file__).parent.parent / "static" / "results.js"
    text = static_js.read_text(encoding="utf-8")
    # Dedicated card renderer exists and excludes SS runs from the legacy grouped path.
    assert "renderSpeedRuns(" in text
    assert "ssRunIds" in text  # SS run_ids identified by normalized identity, not model/position
    # Metric-version badges + legacy banner text are rendered per SS card (v2 / v3 / legacy).
    for needle in ('Metric v2', 'Metric v3', 'Legacy metric', "Legacy prefill semantics"):
        assert needle in text
    # Point metrics are shown individually, with the canonical label distinct from actual input.
    for needle in ("Input", "TTFT", "Prefill", "Generation", "target "):
        assert needle in text


# ---------------------------------------------------------------------------
# Non-Speed legacy results remain available on the flat array (req 11)
# ---------------------------------------------------------------------------

def test_non_speed_legacy_results_remain_available(history_store):
    from src.routes.results import get_past_results
    payload = asyncio_run(get_past_results())
    legacy_rows = [r for r in payload["results"] if r["run_id"] == "legacy-run"]
    assert len(legacy_rows) >= 1
    # Legacy row keeps its canonical byte/token fields intact for the existing view.
    assert legacy_rows[0]["tokens_per_second"] == 42.0
    assert legacy_rows[0]["input_tokens"] == 1200


# ---------------------------------------------------------------------------
# tiny asyncio runner so sync tests can call the async endpoint directly
# ---------------------------------------------------------------------------

import asyncio as _asyncio


def asyncio_run(coro):
    return _asyncio.run(coro)
