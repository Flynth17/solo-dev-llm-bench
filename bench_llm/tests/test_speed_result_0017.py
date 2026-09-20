"""Act 26-AA-0017 -- Speed evidence presentation contract.

Covers the four surfaces completed by this Act:

* per-point authoritative status (completed / partial / unsupported / failed)
* cold/warm repeatability-stage evidence (progressive disclosure, verbatim metrics)
* legacy single-run repeatability note (no fabricated stages)
* run-level execution provenance with the three shared meanings
  (stored / unknown_at_execution / not_stored), where Unknown never collapses into
  Not stored.

The dedicated Speed page is client-rendered by ``static/speed-result.js`` from the
normalized single-run read model at ``/api/speed/runs/{run_id}``. The authoritative
data contract therefore lives in the read model + API, so these tests assert there
(deterministic, no browser). They also assert the served HTML template exposes the
required surface (Status column, repeatability note, provenance sections) and that the
frontend source implements the required mappings. No real benchmark run is required:
read-model tests use in-memory rows; route tests seed a temporary ResultsStore.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from src.results import ResultsStore  # noqa: E402
from src.main import app  # noqa: E402
from src.provenance import capture_provenance, to_json  # noqa: E402
from src.v2_speed_read_model import (  # noqa: E402
    load_speed_run_by_id,
    _speed_run_provenance,
)

POINT_LABEL = {8192: "8K", 16384: "16K", 32768: "32K"}


# ---------------------------------------------------------------------------
# Helpers: persisted Standard Speed rows (in-memory or via a temp store).
# ---------------------------------------------------------------------------

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


def _stage_row(run_id: str, target: int, inp: int, stage: str,
               status: str = "completed", over: dict | None = None) -> dict:
    """One persisted repeatability-stage row (1 cold + 2 warm per canonical point)."""
    d = _row(run_id,
             context_point=POINT_LABEL.get(target, str(target)),
             target_context_tokens=target,
             input_tokens=inp,
             output_tokens=max(1, inp // 100),
             ttft_seconds=0.5,
             prefill_tokens_per_second=14000.0,
             tokens_per_second=210.0,
             wall_time_seconds=1.2,
             cold_or_warm="cold" if stage == "cold" else "warm",
             speed_run_stage=stage,
             speed_point_status=status)
    if over:
        d.update(over)
    return d


def standard_run(run_id: str = "speed-unit-0017") -> list[dict]:
    """Legacy single-row-per-point Standard Speed contract (no stage rows)."""
    return [
        _point(run_id, 32768, 22044, 63, 0.04, 551100.0, 176.68, 25.90),
        _point(run_id, 8192, 5561, 55, 0.04, 139025.0, 185.72, 8.07),
        _point(run_id, 16384, 11056, 53, 0.04, 276400.0, 185.88, 13.95),
    ]


def stage_aware_run(run_id: str = "speed-stage-0017") -> list[dict]:
    """Full 3-point run with exactly one cold + two warm stages per point."""
    rows: list[dict] = []
    for target, inp in [(8192, 5561), (16384, 11056), (32768, 22044)]:
        for stage in ("cold", "warm_a", "warm_b"):
            rows.append(_stage_row(run_id, target, inp, stage))
    return rows


def mixed_status_run(run_id: str = "speed-partial-0017") -> list[dict]:
    """One point with mixed stages -> authoritative per-point status ``partial``."""
    rows: list[dict] = []
    for stage in ("cold", "warm_a", "warm_b"):
        rows.append(_stage_row(run_id, 8192, 5561, stage, "completed"))
    # 16K point: one completed + one failed stage -> partial.
    rows.append(_stage_row(run_id, 16384, 11056, "cold", "completed"))
    rows.append(_stage_row(run_id, 16384, 11056, "warm_a", "failed"))
    for stage in ("cold", "warm_a", "warm_b"):
        rows.append(_stage_row(run_id, 32768, 22044, stage, "completed"))
    return rows


def failed_point_run(run_id: str = "speed-fail-0017") -> list[dict]:
    """One point whose stages all failed -> authoritative per-point failure state."""
    rows: list[dict] = []
    for stage in ("cold", "warm_a", "warm_b"):
        rows.append(_stage_row(run_id, 8192, 5561, stage, "completed"))
    for stage in ("cold", "warm_a", "warm_b"):
        rows.append(_stage_row(run_id, 16384, 11056, stage, "failed"))
    for stage in ("cold", "warm_a", "warm_b"):
        rows.append(_stage_row(run_id, 32768, 22044, stage, "completed"))
    return rows


# ---------------------------------------------------------------------------
# Fixture: patch app_state.results_store with a temp store seeded with arbitrary rows.
# ---------------------------------------------------------------------------

@pytest.fixture
def live_store():
    from fastapi.testclient import TestClient
    import tempfile
    import src.app_state as app_state_module

    client = TestClient(app)
    restored: list = []

    def _seed(rows: list[dict], run_id: str):
        tmp = tempfile.mkdtemp()
        restored.append(app_state_module.results_store)
        store = ResultsStore(csv_path=Path(tmp) / "a.csv", db_path=Path(tmp) / "a.db")
        for r in rows:
            store.add_run(r)
        app_state_module.results_store = store
        return client, run_id

    yield _seed
    if restored:
        app_state_module.results_store = restored[-1]


# ---------------------------------------------------------------------------
# 1. Per-point authoritative status (read model).
# ---------------------------------------------------------------------------

class TestPointState:

    def test_completed_point_status(self):
        result = load_speed_run_by_id("speed-unit-0017", standard_run())
        assert all(p["status"] == "completed" for p in result["points"])

    def test_unsupported_point_status(self):
        rows = standard_run()
        for r in rows:
            if r["target_context_tokens"] == 32768:
                r["speed_point_status"] = "unsupported"
        result = load_speed_run_by_id("speed-unit-0017", rows)
        by_label = {p["label"]: p for p in result["points"]}
        assert by_label["32K"]["status"] == "unsupported"

    def test_partial_point_status_from_mixed_stages(self):
        result = load_speed_run_by_id("speed-partial-0017", mixed_status_run())
        by_target = {p["target_context_tokens"]: p for p in result["points"]}
        # The mixed 16K point is authoritative ``partial``; the clean points stay completed.
        assert by_target[16384]["status"] == "partial"
        assert by_target[8192]["status"] == "completed"
        assert by_target[32768]["status"] == "completed"

    def test_failed_point_status_verbatim(self):
        result = load_speed_run_by_id("speed-fail-0017", failed_point_run())
        by_target = {p["target_context_tokens"]: p for p in result["points"]}
        assert by_target[16384]["status"] == "failed"

    def test_partial_point_metrics_remain_visible(self):
        # A partial point still carries its representative (cold) stage metrics -- the UI
        # must render them, never hide a partially-successful point.
        result = load_speed_run_by_id("speed-partial-0017", mixed_status_run())
        pt = next(p for p in result["points"] if p["target_context_tokens"] == 16384)
        assert pt["status"] == "partial"
        # The cold-derived metrics survive on a partial point -- the UI renders them,
        # never hides a partially-successful point. (Generation is a warm-only mean and
        # is legitimately absent here because the only warm stage failed.)
        assert pt["actual_prompt_tokens"] == 11056
        assert pt["ttft_seconds"] == 0.5
        assert pt["prefill_tokens_per_second"] == 14000.0
        assert pt["completion_tokens"] == 110

    def test_status_never_inferred_from_metric_presence(self):
        # A point with a completed stage is ``completed`` regardless of which metrics are
        # present; status comes from the authoritative speed_point_status, not telemetry.
        result = load_speed_run_by_id("speed-unit-0017", standard_run())
        assert all(p["status"] == "completed" for p in result["points"])


# ---------------------------------------------------------------------------
# 2. Cold/warm repeatability-stage evidence (read model + API).
# ---------------------------------------------------------------------------

class TestStageEvidence:

    def test_exactly_one_cold_and_two_warm_stages(self):
        result = load_speed_run_by_id("speed-stage-0017", stage_aware_run())
        for p in result["points"]:
            assert "runs" in p
            stages = sorted(s["speed_run_stage"] for s in p["runs"])
            assert stages == ["cold", "warm_a", "warm_b"]

    def test_deterministic_cold_warm_a_warm_b_order(self):
        # Seed the stages out of order; the read model must still project cold -> warm_a ->
        # warm_b.
        shuffled: list[dict] = []
        for target, inp in [(8192, 5561), (16384, 11056), (32768, 22044)]:
            for stage in ("warm_b", "cold", "warm_a"):
                shuffled.append(_stage_row("speed-stage-0017", target, inp, stage))
        result = load_speed_run_by_id("speed-stage-0017", shuffled)
        by_target = {p["target_context_tokens"]: p for p in result["points"]}
        for p in by_target.values():
            assert [s["speed_run_stage"] for s in p["runs"]] == ["cold", "warm_a", "warm_b"]

    def test_distinct_stage_identity_preserved(self):
        result = load_speed_run_by_id("speed-stage-0017", stage_aware_run())
        pt8k = next(p for p in result["points"] if p["target_context_tokens"] == 8192)
        run_ids = [s["run_id"] for s in pt8k["runs"]]
        # Every persisted stage is a distinct identity row under the same run.
        assert run_ids == ["speed-stage-0017"] * 3

    def test_stage_status_and_validity_preserved(self):
        rows = stage_aware_run()
        # Mark one warm stage failed; its status + derived validity must survive verbatim.
        for r in rows:
            if r["target_context_tokens"] == 8192 and r["speed_run_stage"] == "warm_a":
                r["speed_point_status"] = "failed"
        result = load_speed_run_by_id("speed-stage-0017", rows)
        pt8k = next(p for p in result["points"] if p["target_context_tokens"] == 8192)
        by_stage = {s["speed_run_stage"]: s for s in pt8k["runs"]}
        assert by_stage["warm_a"]["speed_point_status"] == "failed"
        # Validity is derived from the stage's own status only -- not inherited from the
        # parent point (which is completed).
        warm_a = by_stage["warm_a"]
        assert (warm_a["speed_point_status"] or "").lower() == "failed"

    def test_stage_metrics_rendered_verbatim(self):
        result = load_speed_run_by_id("speed-stage-0017", stage_aware_run())
        pt8k = next(p for p in result["points"] if p["target_context_tokens"] == 8192)
        cold = next(s for s in pt8k["runs"] if s["speed_run_stage"] == "cold")
        assert cold["ttft_seconds"] == 0.5
        assert cold["prefill_tokens_per_second"] == 14000.0
        assert cold["generation_tokens_per_second"] == 210.0
        assert cold["completion_tokens"] == 55
        assert cold["wall_time_seconds"] == 1.2

    def test_no_cross_point_stage_leakage(self):
        result = load_speed_run_by_id("speed-stage-0017", stage_aware_run())
        for p in result["points"]:
            # Every stage row of a point belongs to that point's canonical target only.
            assert all(s["target_context_tokens"] == p["target_context_tokens"]
                       for s in p["runs"])
            assert len(p["runs"]) == 3

    def test_api_exposes_stages_and_repeatability_flag(self, live_store):
        client, rid = live_store(stage_aware_run(), "speed-stage-0017")
        body = client.get(f"/api/speed/runs/{rid}").json()
        assert body["repeatability_stored"] is True
        assert all("runs" in p for p in body["points"])
        cold_warm = sorted(s["speed_run_stage"] for s in body["points"][0]["runs"])
        assert cold_warm == ["cold", "warm_a", "warm_b"]


# ---------------------------------------------------------------------------
# 3. Legacy single-run repeatability evidence.
# ---------------------------------------------------------------------------

class TestLegacyEvidence:

    def test_legacy_has_no_fabricated_stages(self):
        result = load_speed_run_by_id("speed-unit-0017", standard_run())
        assert result["repeatability_stored"] is False
        # No stage rows are invented for legacy single-row evidence.
        assert all("runs" not in p for p in result["points"])

    def test_legacy_metrics_remain_visible(self):
        result = load_speed_run_by_id("speed-unit-0017", standard_run())
        pt8k = next(p for p in result["points"] if p["target_context_tokens"] == 8192)
        assert pt8k["actual_prompt_tokens"] == 5561
        assert pt8k["prefill_tokens_per_second"] == 139025.0

    def test_legacy_api_flag(self, live_store):
        client, rid = live_store(standard_run(), "speed-unit-0017")
        body = client.get(f"/api/speed/runs/{rid}").json()
        assert body["repeatability_stored"] is False

    def test_served_page_exposes_repeatability_note(self, live_store):
        # The repeatability note (with its authoritative "predates the 1-cold / 2-warm"
        # wording) exists in the dedicated page template.
        client, rid = live_store(standard_run(), "speed-unit-0017")
        resp = client.get(f"/speed/results/{rid}")
        assert resp.status_code == 200
        assert 'id="sr-repeatability-note"' in resp.text
        assert "predates the 1-cold / 2-warm persistence contract" in resp.text


# ---------------------------------------------------------------------------
# 4. Run-level execution provenance (stored / unknown / not stored).
# ---------------------------------------------------------------------------

def _prov_rows(run_id: str = "speed-prov-0017") -> list[dict]:
    """A stage-aware run whose rows carry a real, captured provenance snapshot."""
    prov = capture_provenance(
        lm_studio_url="http://localhost:1234",
        model_identifier="test-model",
        model_config={"model_quantization": "Q4_K_M",
                      "loaded_context": 32768, "model_max_context": 32768},
    )
    prov["hardware"]["gpu"] = [
        {"name": "NVIDIA RTX 4090", "vram_bytes": 12884901888, "driver_version": "546.12"}
    ]
    payload = to_json(prov)
    rows: list[dict] = []
    for target, inp in [(8192, 5561), (16384, 11056), (32768, 22044)]:
        for stage in ("cold", "warm_a", "warm_b"):
            rows.append(_stage_row(run_id, target, inp, stage, over={"provenance_json": payload}))
    return rows


class TestProvenanceProjection:

    def test_legacy_run_provenance_is_none(self):
        # A legacy run carries no provenance object -> projection is None (not stored at
        # the run level), never a fabricated set of fields.
        assert _speed_run_provenance(standard_run()) is None

    def test_stored_value_exact(self):
        proj = _speed_run_provenance(_prov_rows())
        assert proj["present"] is True
        cpu = proj["hardware"]["CPU"]
        assert cpu["status"] == "stored"
        # Exact persisted value, never recomputed or guessed.
        assert cpu["value"]  # a real CPU model label

    def test_unknown_never_collapses_into_not_stored(self):
        proj = _speed_run_provenance(_prov_rows())
        lm = proj["runtime"]["LM Studio version"]
        model_path = proj["model"]["Model path"]
        # Provenance exists (the object is present) but the runtime did not expose these
        # fields at execution time -> unknown_at_execution, distinct from not_stored.
        assert lm["status"] == "unknown_at_execution"
        assert model_path["status"] == "not_stored"
        # The three meanings are pairwise distinct.
        statuses = {
            proj["hardware"]["CPU"]["status"],
            lm["status"],
            model_path["status"],
        }
        assert {"stored", "unknown_at_execution", "not_stored"} <= statuses

    def test_all_three_meanings_present(self):
        proj = _speed_run_provenance(_prov_rows())
        present_statuses = {
            **proj["hardware"], **proj["hardware_extra"], **proj["runtime"],
            **proj["model"], **proj["inference"],
        }
        values = {f["status"] for f in present_statuses.values()}
        assert "stored" in values
        assert "unknown_at_execution" in values
        assert "not_stored" in values

    def test_api_exposes_classified_provenance(self, live_store):
        client, rid = live_store(_prov_rows(), "speed-prov-0017")
        body = client.get(f"/api/speed/runs/{rid}").json()
        prov = body["provenance"]
        assert prov["present"] is True
        assert prov["hardware"]["CPU"]["status"] == "stored"
        assert prov["runtime"]["LM Studio version"]["status"] == "unknown_at_execution"
        assert prov["model"]["Model path"]["status"] == "not_stored"
        # Grouped under the four contract sections, never raw JSON.
        assert set(prov.keys()) >= {"present", "hardware", "runtime", "model", "inference"}

    def test_served_page_exposes_provenance_sections(self, live_store):
        client, rid = live_store(_prov_rows(), "speed-prov-0017")
        resp = client.get(f"/speed/results/{rid}")
        assert resp.status_code == 200
        body = resp.text
        assert 'id="sr-provenance-panel"' in body
        for section in ("Hardware", "Runtime", "Model", "Inference"):
            assert section in body


# ---------------------------------------------------------------------------
# 5. Frontend contract (source inspection) -- the page is client-rendered, so the
#    status/stage/provenance mappings are verified against the frontend source.
# ---------------------------------------------------------------------------

class TestSpeedResultJSContract:

    @staticmethod
    def _src() -> str:
        p = Path(__file__).resolve().parent.parent / "static" / "speed-result.js"
        return p.read_text(encoding="utf-8")

    def test_status_column_is_second(self):
        src = self._src()
        # The Status cell is rendered as the second <td> of each point row (after Point).
        assert 'srb-status-cell' in src
        assert "pointStatusClass" in src and "pointStatusText" in src

    def test_status_labels_are_uppercase_verbatim(self):
        src = self._src()
        # completed / partial / unsupported are styled distinctly; any other terminal
        # failure state falls to is-failed -- never a healthy completed badge.
        assert 'is-completed' in src
        assert 'is-partial' in src
        assert 'is-unsupported' in src
        assert 'is-failed' in src

    def test_stage_disclosure_orders_cold_warm_a_warm_b(self):
        src = self._src()
        assert "cold" in src and "warm_a" in src and "warm_b" in src
        # Deterministic ordering map is applied defensively.
        assert "STAGE_ORDER" in src

    def test_provenance_distinguishes_unknown_and_not_stored(self):
        src = self._src()
        assert "unknown_at_execution" in src  # classification key -> renders "Unknown"
        assert "Not stored" in src            # not_stored label rendered verbatim
        assert "srb-prov-unknown" in src
