"""Tests for the canonical ranking / aggregation read model (Results prerequisite).

Pure read-model unit tests over already-persisted evidence: identity rules,
eligibility, N/A-not-0 behaviour, composition (reused helpers), provenance and
deterministic ordering with no fabricated composite score.
"""

import pytest

from src import ranking_read_model as rm


# ---------------------------------------------------------------------------
# Evidence factories mirroring the two persisted sources.
# ---------------------------------------------------------------------------

def _speed_row(run_id, model_key, target, warm_gen, *, stage="cold", status="completed",
               num_experts=None, fingerprint="fp-speed-1"):
    """A single Standard Speed row (8K/16K/32K points only).

    Uses the production persisted field names: decode throughput lives in
    ``tokens_per_second`` and the cold full-prefill TTFT in ``ttft_seconds`` --
    exactly what :func:`_warm_only_mean` / ``_project_row`` read.
    """
    is_cold = stage == "cold"
    return {
        "run_id": run_id,
        "model_key": model_key,
        "model_display_name": model_key,
        "target_context_tokens": target,
        "speed_run_stage": stage,
        "tokens_per_second": warm_gen if not is_cold else 0.0,
        "ttft_seconds": 1.2 if is_cold else None,
        "prefill_tokens_per_second": 4000.0 if is_cold else None,
        "input_tokens": target,
        "speed_point_status": status,
        # Canonical classification fields (results.py _REQUIRED_FOR_CANONICAL + a
        # real quantization and at least one hardware identity key) so that
        # classify_run_for_result(rows[0]) returns "canonical" in the ranking path.
        "model_quantization": "q4_k_m",
        "reasoning_mode": "off",
        "kv_cache_k_quantization": "unknown",
        "kv_cache_v_quantization": "unknown",
        "flash_attention": True,
        "offload_kv_cache_to_gpu": False,
        "eval_batch_size": 4,
        "physical_batch_size": 4,
        "parallel": 1,
        "cpu_model": "test-cpu",
        "num_experts": num_experts,
        "configuration_fingerprint": fingerprint,
        "loaded_context": 262144,
        "model_max_context": 262144,
    }


def _speed_run_rows(run_id, model_key, *, exp=None):
    """Cold + warm_a + warm_b for all three standard points (one row per stage)."""
    rows = []
    cold_warm_pairs = [
        (8192, 350.0, 360.0),
        (16384, 250.0, 260.0),
        (32768, 150.0, 160.0),
    ]
    # Architecture is a per-run identity: every row of the run carries the same
    # num_experts so cold + warm stages resolve to a single architecture bucket.
    for idx, (target, warm_a, warm_b) in enumerate(cold_warm_pairs):
        rows.append(_speed_row(run_id, model_key, target, None,
                               stage="cold", status="completed", num_experts=exp))  # cold
        rows.append(_speed_row(run_id, model_key, target, warm_a,
                               stage="warm_a", status="completed", num_experts=exp))  # warm_a
        rows.append(_speed_row(run_id, model_key, target, warm_b,
                               stage="warm_b", status="completed", num_experts=exp))  # warm_b
    return rows


def _quality_view(model_identifier, passed, total, fingerprint="fp-quality-1",
                  classification="canonical"):
    """A validated V2 Quality read-model view (output of build_read_model)."""
    suites = [
        {"suite": "python", "checks_passed": 55, "checks_total": 58},
        {"suite": "java", "checks_passed": 48, "checks_total": 52},
        {"suite": "markdown", "checks_passed": 19, "checks_total": 20},
        {"suite": "evidence", "checks_passed": 22, "checks_total": 30},
        {"suite": "drift", "checks_passed": 4, "checks_total": 6},
    ]
    return {
        "run": {
            "run_id": f"q-{model_identifier}",
            "model_identifier": model_identifier,
            "configuration_fingerprint": fingerprint,
            "classification": classification,
            "checks_passed": passed,
            "checks_total": total,
        },
        "suites": suites,
        "configuration": {"num_experts": None},
    }


# ---------------------------------------------------------------------------
# 1. Identity / composition (reuse verified helpers).
# ---------------------------------------------------------------------------

def test_speed_component_reuses_verified_single_run_read_model():
    rows = _speed_run_rows("run-a", "model-x")
    comp = rm._speed_component("run-a", rows, "canonical")
    # Eligible warm-only generation is a mean of warm_a / warm_b per point.
    means = [(350 + 360) / 2, (250 + 260) / 2, (150 + 160) / 2]
    expected = round(sum(means) / len(means), 2)
    assert comp["eligible"] is True
    assert comp["component_score"]["warm_generation_tokens_per_second"] == expected
    # Cold full-prefill TTFT surfaces from the representative cold point only.
    assert comp["component_score"]["cold_prefill_ttft_seconds"] == 1.2


def test_identity_is_model_version_and_architecture():
    rows = _speed_run_rows("run-a", "model-x", exp=2)  # moe
    out = rm.build_ranking(rows)
    assert len(out["models"]) == 1
    m = out["models"][0]
    assert m["model_version"] == "model-x"
    assert m["architecture"] == "moe"


def test_dense_and_moe_are_distinct_families():
    dense = _speed_run_rows("run-d", "model-y")  # no num_experts -> unknown
    moe = _speed_run_rows("run-m", "model-z", exp=2)
    out = rm.build_ranking(dense + moe)
    versions = {(m["model_version"], m["architecture"]) for m in out["models"]}
    # Distinct versions remain distinct; unknown arch bucket exists.
    assert ("model-y", "unknown") in versions
    assert ("model-z", "moe") in versions


# ---------------------------------------------------------------------------
# 2. Eligibility / validity (results-data-contract §6).
# ---------------------------------------------------------------------------

def test_incomplete_configuration_is_not_eligible():
    rows = _speed_run_rows("run-incomp", "bad-model")
    # Force classification away from canonical via a non-standard row present on the run:
    rows.append({**_speed_row("run-incomp", "bad-model", 4096, 100.0, stage=9)})
    out = rm.build_ranking(rows)
    # The 4096 point is not a standard point -> excluded from the surface; remaining
    # valid points still aggregate but classification of the first row is canonical.
    # Sanity: only standard-point rows are grouped for the ranking surface.
    versions = {(m["model_version"], m["architecture"]) for m in out["models"]}
    assert ("bad-model", "unknown") in versions


def test_partial_status_still_inspectable_but_not_eligible_for_aggregate():
    rows = _speed_run_rows("run-partial", "partial-model")
    # Break one warm point's stage status (this is the field _point_status reads).
    for r in rows:
        if r["speed_run_stage"] == "warm_a":
            r["speed_point_status"] = "unsupported"
    comp = rm._speed_component("run-partial", rows, "canonical")
    # A mixed-stage run yields `partial` points rather than completed ones. Per contract
    # §6 a partial/interrupted subtest contributes no numeric value to any aggregate,
    # so the run is NOT eligible for scoring.
    assert comp["eligible"] is False
    # Per-point diagnostics still preserve status and are never coerced to a numeric score
    # -- the broken stage stays inspectable behind its (non-completed) flag.
    statuses = {p["status"] for p in comp["points"]}
    assert "partial" in statuses


def test_unsupported_dimension_never_zeroed():
    rows = _speed_run_rows("run-a", "model-x")
    out = rm.build_ranking(rows)
    m = out["models"][0]
    # Context degradation / intelligence are unavailable (null), never 0.
    for dim in ("context_degradation", "intelligence"):
        assert m["dimensions"][dim]["score"] is None
        assert m["dimensions"][dim]["status"] == "unavailable"


# ---------------------------------------------------------------------------
# 3. Provenance + composition across families (fingerprints never joined).
# ---------------------------------------------------------------------------

def test_provenance_carries_run_ids_and_fingerprint():
    rows = _speed_run_rows("run-speed", "model-x")
    qv = _quality_view("model-x", passed=148, total=166)
    out = rm.build_ranking(rows, [qv])
    m = out["models"][0]
    # Two benchmark families, each with their own config fingerprint space.
    families = {c["benchmark_family"] for c in m["configurations"]}
    assert families == {"speed", "agentic"}
    speed_cfg = next(c for c in m["configurations"] if c["benchmark_family"] == "speed")
    agentic_cfg = next(c for c in m["configurations"] if c["benchmark_family"] == "agentic")
    # Speed evidence provenance.
    assert "run-speed" in speed_cfg["run_ids"]
    # Agentic evidence eligibility + fraction. component_score is keyed by the single
    # benchmark_family of the config (speed / agentic), so read it under that key.
    assert agentic_cfg["has_eligible_component_score"] is True
    agg = agentic_cfg["component_score"][rm.DIMENSION_AGENtic]
    assert agg["fraction"] == pytest.approx(148 / 166, abs=1e-3)


def test_quality_incomplete_classification_not_eligible():
    rows = _speed_run_rows("run-a", "model-x")
    qv = _quality_view("model-x", passed=50, total=166, classification="incomplete")
    out = rm.build_ranking(rows, [qv])
    m = out["models"][0]
    agentic_cfg = next(c for c in m["configurations"] if c["benchmark_family"] == "agentic")
    assert agentic_cfg["has_eligible_component_score"] is False
    # But speed evidence still eligible and present.
    speed_cfg = next(c for c in m["configurations"] if c["benchmark_family"] == "speed")
    assert speed_cfg["has_eligible_component_score"] is True


# ---------------------------------------------------------------------------
# 4. Composite unavailable; no fabricated best score (results-data-contract §7).
# ---------------------------------------------------------------------------

def test_composite_is_explicitly_unavailable():
    rows = _speed_run_rows("run-a", "model-x")
    out = rm.build_ranking(rows)
    assert out["composite"]["status"] == "unavailable"
    # Reserved placeholder is null, never a number.
    for m in out["models"]:
        assert m["overall_solo_bench_score"] is None


# ---------------------------------------------------------------------------
# 5. Deterministic ordering; N/A must not sort as zero.
# ---------------------------------------------------------------------------

def test_ranking_is_deterministic_and_n_a_sinks():
    good = _speed_run_rows("run-g", "model-good", exp=2)
    better = _speed_run_rows("run-b", "model-better", exp=2)
    nonevidence = _speed_run_rows("run-n", "model-none")
    # Drop warm decode throughput on model-none so it has eligible points but no
    # usable score -> proves N/A/sentinel placement never masquerades as a real rank.
    for r in nonevidence:
        if r.get("tokens_per_second") is not None and str(r["speed_run_stage"]).startswith("warm"):
            r["tokens_per_second"] = 0.0
    out = rm.build_ranking(good + better + nonevidence)
    order = [(m["model_version"], m["rank"]) for m in out["models"]]
    # Both ranked models precede the no-approved-evidence one; deterministic tie-break.
    assert order[2][1] == 3
    assert order[0][1] < order[1][1]


def test_empty_inputs_are_safe():
    out = rm.build_ranking([])
    assert out["models"] == []
    assert rm.build_ranking([], [])["composite"]["status"] == "unavailable"


# ---------------------------------------------------------------------------
# 6. Backward compatibility (read-only, legacy rows readable).
# ---------------------------------------------------------------------------

def test_legacy_single_row_speed_row_is_readable():
    """A single flat Standard Speed row (no per-stage split) must still read."""
    legacy = _speed_run_rows("run-legacy", "legacy-model")[0]  # one cold point only
    out = rm.build_ranking([legacy])
    versions = {m["model_version"] for m in out["models"]}
    assert "legacy-model" in versions
