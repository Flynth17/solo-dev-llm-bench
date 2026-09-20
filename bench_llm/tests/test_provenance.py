"""Act (reproducibility) — run-level execution provenance.

These tests prove that every new BenchLLM benchmark run carries an immutable,
run-level *provenance* snapshot of the hardware / runtime / model / inference
configuration that produced its evidence, captured ONCE at benchmark start and
stored at the RUN level (never duplicated per suite/request/stage).

They also prove the historical-compatibility contract:

* legacy runs without provenance still load,
* legacy runs are never backfilled from the current machine,
* the three field meanings are distinct and never collapsed:
    - ``NOT STORED``           (no provenance persisted -- a legacy run),
    - ``UNKNOWN AT EXECUTION`` (provenance exists but the runtime did not expose it),
    - ``CURRENT VALUE``        (a real, captured value).

And that scoring/classification are untouched by the addition.

Isolation: every persistence test uses an isolated temp store; no test touches the
real active V2 dataset at ``data/benchmark_results.*`` or appends rows to it.
"""

import json
import tempfile
from pathlib import Path

import src.v2_quality_artifact as _qa
from src import provenance as P
from src.provenance import (
    UNKNOWN_EXECUTION,
    capture_provenance,
    field_status,
    from_json,
    provenance_present,
    to_json,
)
from src.results import (
    ResultsStore,
    classify_run_for_result,
    compute_configuration_fingerprint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _temp_store():
    """Return a fresh isolated (ResultsStore, tmp_dir) pair for one test."""
    tmp = Path(tempfile.mkdtemp())
    store = ResultsStore(csv_path=tmp / "r.csv", db_path=tmp / "r.db")
    return store, tmp


class _SuiteDoc:
    """Minimal stand-in for a SuiteResult (exposes to_dict())."""

    def __init__(self, **fields):
        self._fields = fields

    def to_dict(self):
        return dict(self._fields)


def _model_config(quant="Q4_K_M", loaded_context=32768, max_context=262144):
    return {
        "model_key": "ornith-1.5-35b-a3b",
        "model_quantization": quant,
        "loaded_context": loaded_context,
        "model_max_context": max_context,
        "reasoning_mode": "off",
        "flash_attention": False,
        "offload_kv_cache_to_gpu": True,
    }


def _workflow_document(aggregate, with_provenance=True):
    """Build a workflow-style run document (mirrors build_run_document inputs).

    Injects ``checks_total`` so the artifact builder's structural guard passes.
    """
    aggregate = dict(aggregate)
    aggregate.setdefault("checks_total", 166)
    suites = [
        _SuiteDoc(suite="python", requests=[{"request_id": "PY-1"}, {"request_id": "PY-2"}]),
        _SuiteDoc(suite="java", requests=[{"request_id": "JAVA-1"}]),
    ]
    if not with_provenance:
        aggregate = {k: v for k, v in aggregate.items() if k != "provenance"}
    return _qa.build_run_document(aggregate, suites)


# ---------------------------------------------------------------------------
# 1. A new run persists provenance
# ---------------------------------------------------------------------------

def test_01_new_run_persists_provenance_workflow():
    prov = capture_provenance(
        lm_studio_url="http://localhost:1234",
        model_identifier="ornith-1.5-35b-a3b",
        model_config=_model_config(),
    )
    agg = {
        "run_id": "run-prov-001",
        "model_identifier": "ornith-1.5-35b-a3b",
        "configuration_fingerprint": "fp",
        "classification": "incomplete",
        "reasoning_policy": "inherit",
        "provenance": prov,
    }
    doc = _workflow_document(agg)
    assert provenance_present(doc) is True
    stored = doc["provenance"]
    # All four sections present and versioned.
    assert stored["schema_version"] == P.PROVENANCE_SCHEMA_VERSION
    for section in ("hardware", "runtime", "model", "inference"):
        assert section in stored and stored[section], f"missing {section}"
    assert stored["model"]["quantization"] == "Q4_K_M"


def test_01b_new_run_persists_provenance_speed():
    store, _tmp = _temp_store()
    prov = capture_provenance(
        lm_studio_url="http://localhost:1234",
        model_identifier="ornith-1.5-35b-a3b",
        model_config=_model_config(),
    )
    identity = {"run_id": "run-speed-prov", "model_key": "ornith-1.5-35b-a3b"}
    identity["provenance_json"] = to_json(prov)
    row = dict(identity)
    row.update({"speed_run_stage": "cold", "tokens_per_second": 12.3})
    store.add_run(row)
    reloaded = next(r for r in store.get_all() if r["run_id"] == "run-speed-prov")
    assert reloaded["provenance_json"]  # persisted, non-blank
    parsed = from_json(reloaded["provenance_json"])
    assert parsed is not None
    assert parsed["model"]["quantization"] == "Q4_K_M"


# ---------------------------------------------------------------------------
# 2. Hardware metadata is run-level, NOT duplicated per request/suite
# ---------------------------------------------------------------------------

def test_02_hardware_is_run_level_not_per_request():
    prov = capture_provenance(
        lm_studio_url="http://localhost:1234",
        model_identifier="ornith-1.5-35b-a3b",
        model_config=_model_config(),
    )
    agg = {
        "run_id": "run-prov-002",
        "model_identifier": "ornith-1.5-35b-a3b",
        "configuration_fingerprint": "fp",
        "classification": "incomplete",
        "reasoning_policy": "inherit",
        "provenance": prov,
    }
    doc = _workflow_document(agg)
    # Provenance lives at the RUN (document) level...
    assert provenance_present(doc) is True
    # ...and is NOT duplicated into any suite / request record.
    for suite in doc["suites"]:
        assert "provenance" not in suite, "provenance leaked into a suite record"
        for req in suite.get("requests", []):
            assert "provenance" not in req, "provenance leaked into a request record"


def test_02b_speed_provenance_identical_across_stages():
    store, _tmp = _temp_store()
    prov = capture_provenance(
        lm_studio_url="http://localhost:1234",
        model_identifier="ornith-1.5-35b-a3b",
        model_config=_model_config(),
    )
    prov_json = to_json(prov)
    identity = {"run_id": "run-speed-2", "model_key": "x"}
    identity["provenance_json"] = prov_json
    store.add_run(dict(identity, speed_run_stage="cold"))
    store.add_run(dict(identity, speed_run_stage="warm_a"))
    store.add_run(dict(identity, speed_run_stage="warm_b"))
    rows = [r for r in store.get_all() if r["run_id"] == "run-speed-2"]
    assert len(rows) == 3
    # One captured object shared across every stage -- not recomputed per stage.
    assert all(r["provenance_json"] == prov_json for r in rows)


# ---------------------------------------------------------------------------
# 3. Model quantization is persisted SEPARATELY from KV quantization
# ---------------------------------------------------------------------------

def test_03_model_quant_separate_from_kv_quant():
    prov = capture_provenance(
        lm_studio_url="http://localhost:1234",
        model_identifier="ornith-1.5-35b-a3b",
        model_config=_model_config(quant="Q4_K_M"),
    )
    weight_quant = prov["model"]["quantization"]
    kv_k = prov["inference"]["kv_cache_k_type"]
    kv_v = prov["inference"]["kv_cache_v_type"]
    assert weight_quant == "Q4_K_M"
    # KV cache quant is a DIFFERENT concept and must never equal the weight quant.
    assert kv_k != weight_quant
    assert kv_v != weight_quant


# ---------------------------------------------------------------------------
# 4. Unknown KV configuration remains UNKNOWN rather than guessed
# ---------------------------------------------------------------------------

def test_04_unknown_kv_stays_unknown():
    prov = capture_provenance(
        lm_studio_url="http://localhost:1234",
        model_identifier="ornith-1.5-35b-a3b",
        model_config=_model_config(),
    )
    # LM Studio does not expose KV cache quant -> explicit unknown, never a guess.
    assert prov["inference"]["kv_cache_k_type"] == UNKNOWN_EXECUTION
    assert prov["inference"]["kv_cache_v_type"] == UNKNOWN_EXECUTION
    for guessed in ("Q8_0", "Q4_0", "fp32", "Q4_K_M"):
        assert prov["inference"]["kv_cache_k_type"] != guessed
    # field_status distinguishes the three meanings.
    assert field_status(None) == P.NOT_STORED
    assert field_status(UNKNOWN_EXECUTION) == "unknown_at_execution"
    assert field_status("Q4_K_M") == "stored"


# ---------------------------------------------------------------------------
# 5. Legacy runs without provenance still load
# ---------------------------------------------------------------------------

def test_05_legacy_runs_without_provenance_still_load():
    # A legacy aggregate carries NO provenance key at all.
    legacy_agg = {
        "run_id": "run-legacy",
        "model_identifier": "old-model",
        "configuration_fingerprint": "fp",
        "classification": "incomplete",
        "reasoning_policy": "inherit",
    }
    doc = _workflow_document(legacy_agg, with_provenance=False)
    assert "provenance" not in doc
    assert provenance_present(doc) is False


def test_05b_legacy_speed_row_loads():
    store, _tmp = _temp_store()
    # A pre-provenance row: no provenance_json key at all.
    legacy = {"run_id": "run-legacy-speed", "model_key": "x", "tokens_per_second": 9.9}
    store.add_run(legacy)
    row = next(r for r in store.get_all() if r["run_id"] == "run-legacy-speed")
    assert not row.get("provenance_json")  # blank, loads fine
    assert provenance_present(row) is False


# ---------------------------------------------------------------------------
# 6. Legacy runs are NOT backfilled from the current machine
# ---------------------------------------------------------------------------

def test_06_legacy_not_backfilled_from_current_machine():
    store, _tmp = _temp_store()
    # The current machine DOES have capturable hardware (provenance works here).
    live = capture_provenance(model_identifier="x")
    assert live["hardware"]["cpu_model"]

    legacy = {"run_id": "run-no-backfill", "model_key": "x"}
    store.add_run(legacy)
    row = next(r for r in store.get_all() if r["run_id"] == "run-no-backfill")
    # NOT backfilled with the live machine's hardware.
    assert not row.get("provenance_json")
    parsed = from_json(row.get("provenance_json"))
    assert parsed is None
    # The definitive check: no current-machine hardware leaked into the legacy row,
    # even though capture_provenance works on this host (see the live snapshot above).
    assert not (row.get("cpu_model") or "").strip()


# ---------------------------------------------------------------------------
# 7. Provenance survives serialization / deserialization unchanged
# ---------------------------------------------------------------------------

def test_07_provenance_round_trips_through_serialization():
    prov = capture_provenance(
        lm_studio_url="http://localhost:1234",
        model_identifier="ornith-1.5-35b-a3b",
        model_config=_model_config(),
        captured_at="2026-09-13T21:14:10.506354+00:00",
    )
    # via the module helper
    assert from_json(to_json(prov)) == prov
    # via stdlib json (the on-disk format)
    blob = json.dumps(prov, sort_keys=True, default=str)
    assert json.loads(blob) == prov


# ---------------------------------------------------------------------------
# 8. Different runtime configurations produce distinguishable provenance
# ---------------------------------------------------------------------------

def test_08_different_runtime_configs_distinguishable():
    base = _model_config()
    a = capture_provenance(
        lm_studio_url="http://localhost:1234",
        model_identifier="ornith-1.5-35b-a3b",
        model_config=base,
    )
    # Same machine, different backend URL -> runtime section differs.
    b = capture_provenance(
        lm_studio_url="http://localhost:9999",
        model_identifier="ornith-1.5-35b-a3b",
        model_config=base,
    )
    assert a["runtime"]["backend_url"] != b["runtime"]["backend_url"]
    assert a != b

    # Same backend, different inference config (flash attention) -> inference differs.
    c = dict(base)
    c["flash_attention"] = True
    c2 = capture_provenance(
        lm_studio_url="http://localhost:1234",
        model_identifier="ornith-1.5-35b-a3b",
        model_config=c,
    )
    assert a["inference"]["flash_attention"] != c2["inference"]["flash_attention"]
    assert a != c2


# ---------------------------------------------------------------------------
# 9. Existing benchmark scoring / classification behaviour is unchanged
# ---------------------------------------------------------------------------

def test_09_scoring_and_classification_unchanged_by_provenance():
    cfg = _model_config(quant="Q4_K_M")
    base_run = {
        "run_id": "run-score",
        "model_key": "ornith-1.5-35b-a3b",
        "model_quantization": "Q4_K_M",
        "loaded_context": 32768,
        "model_max_context": 262144,
        "reasoning_mode": "off",
        "kv_cache_k_quantization": None,
        "kv_cache_v_quantization": None,
        "flash_attention": False,
        "offload_kv_cache_to_gpu": True,
        "eval_batch_size": 1,
        "physical_batch_size": 1,
        "parallel": 1,
        "output_budget_policy": "75_percent_context",
        "cpu_model": "Test CPU",  # hardware-identity key -> classifies cleanly
    }
    with_prov = dict(base_run)
    with_prov["provenance"] = capture_provenance(
        lm_studio_url="http://localhost:1234",
        model_identifier="ornith-1.5-35b-a3b",
        model_config=cfg,
    )

    # The fingerprint is computed over material config keys only -> provenance invisible.
    assert compute_configuration_fingerprint(base_run) == compute_configuration_fingerprint(
        with_prov
    )
    # Classification is likewise unaffected by the presence of a provenance object.
    assert classify_run_for_result(base_run) == classify_run_for_result(with_prov)


def test_09b_existing_fingerprint_and_classification_tests_pass():
    # Regression guard: an existing-style canonical run still classifies as canonical and
    # has a deterministic fingerprint (mirrors tests.test_act11b_config_fingerprint).
    from src.results import BENCHMARK_REASONING_MODE

    def _run(**over):
        r = {
            "model_key": "qwen3.8-27b",
            "model_quantization": "Q5_K_M",
            "loaded_context": 262144,
            "reasoning_mode": BENCHMARK_REASONING_MODE,
            "kv_cache_k_quantization": "Q8_0",
            "kv_cache_v_quantization": "Q8_0",
            "flash_attention": True,
            "offload_kv_cache_to_gpu": True,
            "eval_batch_size": 1,
            "physical_batch_size": 1,
            "parallel": 1,
            "cpu_model": "Test CPU",  # hardware-identity key -> classifies canonical
        }
        r.update(over)
        return r

    a = _run()
    b = _run()
    assert compute_configuration_fingerprint(a) == compute_configuration_fingerprint(b)
    assert classify_run_for_result(a) == "canonical"
