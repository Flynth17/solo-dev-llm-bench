"""Act 11B — V2 configuration identity, fingerprint and canonical/incomplete.

A BenchLLM V2 result identifies the MODEL + INFERENCE CONFIGURATION, not merely
the model name. These tests prove:

* loaded / max context are captured as distinct fields,
* K/V cache quantization provenance (api/manual/unknown) is handled without
  fabrication,
* MTP/speculative parameters persist and differentiate fingerprints,
* the fingerprint is deterministic, sensitive to one material change, and ignores
  transient instance identifiers,
* runs are classified canonical vs incomplete, and incomplete runs still persist.

Isolation: every test that persists uses an isolated temp store (``_temp_paths``).
No test touches the real active V2 dataset at ``data/benchmark_results.*``.
"""

import csv
import hashlib
import tempfile
from pathlib import Path

import pytest

from src.results import (
    BENCHMARK_REASONING_MODE,
    CSV_HEADERS,
    ResultsStore,
    classify_run_for_result,
    compute_configuration_fingerprint,
    normalize_loaded_instance_config,
)

# --- temp-store isolation helper -------------------------------------------


def _temp_paths():
    """Generator yielding a single (csv_path, db_path) pair per iteration so callers
    write ``for csv_p, db_p in _temp_paths():``. Uses tmp dirs only — never the real
    active dataset. Guarantees these tests add ZERO rows to it."""
    tmp = Path(tempfile.mkdtemp())
    csv_p = tmp / "results.csv"
    db_p = tmp / "results.db"
    assert str(csv_p).startswith(str(Path(tempfile.gettempdir())))
    try:
        yield csv_p, db_p
    finally:
        for p in (csv_p, db_p):
            if p.exists():
                p.unlink()


# --- row builders ------------------------------------------------------------

KV_DEFAULTS = {"kv_cache_k_quantization": "Q8_0", "kv_cache_v_quantization": "Q8_0"}


def _base_run(**overrides):
    """A complete, canonical V2 run carrying every material config field."""
    run = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "run_id": "v2-fp-001",
        "model_key": "qwen3.8-27b",
        "model_display_name": "Qwen 3.8 27B",
        "model_quantization": "Q5_K_M",
        "hardware_label": "HW",
        "execution_environment": "Local",
        "connection_type": "",
        "iteration": 1,
        "cold_or_warm": "warm",
        "tokens_per_second": 45.0,
        "ttft_seconds": 0.4,
        "input_tokens": 128,
        "output_tokens": 64,
        "max_output_tokens": 500,
        "temperature": 0.0,
        # Context (kept distinct).
        "model_max_context": 262144,
        "loaded_context": 262144,
        "prompt_tokens": 128,
        # First-class inference config.
        "reasoning_mode": BENCHMARK_REASONING_MODE,
        "flash_attention": True,
        "offload_kv_cache_to_gpu": True,
        "eval_batch_size": 2048,
        "physical_batch_size": 512,
        "parallel": 1,
        "num_experts": 8,
        # MTP/speculative OFF.
        "speculative_draft_mtp": False,
        "speculative_draft_simple": False,
        "speculative_draft_model": "",
        "speculative_draft_max_tokens": 3,
        "speculative_draft_min_tokens": 0,
        "speculative_draft_min_continue_probability": 0,
        # KV cache quantization (api-sourced in this baseline).
        "kv_cache_k_quantization": "Q8_0",
        "kv_cache_v_quantization": "Q8_0",
        "kv_cache_quantization_source": "api",
        "lmstudio_instance_config_json": '{"context_length": 262144}',
        # Hardware identity (already captured by existing telemetry/metadata).
        "cpu_model": "AMD Ryzen 9 9950X3D",
        "gpu_model": "NVIDIA RTX 5090",
    }
    run.update(overrides)
    return run


def _canonical_instance(context_length=262144):
    """A synthetic LM Studio loaded-instance object (no network)."""
    return {
        "id": "qwen3.8-27b",
        "config": {
            "context_length": context_length,
            "eval_batch_size": 2048,
            "physical_batch_size": 512,
            "parallel": 1,
            "flash_attention": True,
            "num_experts": 8,
            "offload_kv_cache_to_gpu": True,
            "speculative_draft_mtp": False,
            "speculative_draft_simple": False,
            "speculative_draft_model": "",
            "speculative_draft_max_tokens": 3,
            "speculative_draft_min_tokens": 0,
            "speculative_draft_min_continue_probability": 0,
        },
        "remaining_ttl_seconds": 3600,  # transient — must be excluded from raw json
    }


# ---------------------------------------------------------------------------
# Context handling
# ---------------------------------------------------------------------------
class TestContext:
    def test_loaded_context_extracted_from_instance_config(self):
        cfg = normalize_loaded_instance_config(_canonical_instance())
        assert cfg["loaded_context"] == 262144

    def test_loaded_and_max_context_are_distinct_columns(self):
        # Distinct keys, independently populated (not conflated).
        run = _base_run(model_max_context=262144, loaded_context=950 * 1024)
        assert "model_max_context" in CSV_HEADERS
        assert "loaded_context" in CSV_HEADERS
        assert run["loaded_context"] != run["model_max_context"]

    def test_950k_vs_1m_produce_different_fingerprints(self):
        a = _base_run(loaded_context=950 * 1024)
        b = _base_run(loaded_context=1024 * 1024)
        assert compute_configuration_fingerprint(a) != compute_configuration_fingerprint(b)


# ---------------------------------------------------------------------------
# K/V cache provenance
# ---------------------------------------------------------------------------
class TestKVCache:
    def test_api_sourced_value_persisted_and_recorded(self):
        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            store.add_run(_base_run(**{"kv_cache_k_quantization": "Q8_0",
                                       "kv_cache_v_quantization": "Q8_0",
                                       "kv_cache_quantization_source": "api"}))
            row = store.get_all()[0]
            assert row["kv_cache_k_quantization"] == "Q8_0"
            assert row["kv_cache_v_quantization"] == "Q8_0"
            assert row["kv_cache_quantization_source"] == "api"

    def test_manual_sourced_value_persisted_and_recorded(self):
        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            run = _base_run(**{"kv_cache_k_quantization": "Q4_0",
                               "kv_cache_v_quantization": "Q4_0",
                               "kv_cache_quantization_source": "manual"})
            store.add_run(run)
            row = store.get_all()[0]
            assert row["kv_cache_k_quantization"] == "Q4_0"
            assert row["kv_cache_v_quantization"] == "Q4_0"
            assert row["kv_cache_quantization_source"] == "manual"

    def test_unknown_remains_none(self):
        run = _base_run(**{"kv_cache_k_quantization": None,
                           "kv_cache_v_quantization": None,
                           "kv_cache_quantization_source": "unknown"})
        assert compute_configuration_fingerprint(run)  # still fingerprintable
        # Unknown K/V -> not canonical (a required value is missing).
        assert classify_run_for_result(run) == "incomplete"

    def test_q4q4_vs_q8q8_produce_different_fingerprints(self):
        q4 = _base_run(**{"kv_cache_k_quantization": "Q4_0",
                          "kv_cache_v_quantization": "Q4_0"})
        q8 = _base_run(**{"kv_cache_k_quantization": "Q8_0",
                          "kv_cache_v_quantization": "Q8_0"})
        assert compute_configuration_fingerprint(q4) != compute_configuration_fingerprint(q8)


# ---------------------------------------------------------------------------
# MTP / speculative decoding
# ---------------------------------------------------------------------------
class TestMTP:
    def test_mtp_off_handled_correctly(self):
        run = _base_run(speculative_draft_mtp=False,
                        speculative_draft_simple=False,
                        speculative_draft_model="")
        # OFF must not require a draft model to be canonical.
        assert classify_run_for_result(run) == "canonical"
        fp = compute_configuration_fingerprint(run)
        assert isinstance(fp, str) and len(fp) == 64

    def test_mtp_on_parameters_persisted(self):
        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            run = _base_run(
                speculative_draft_mtp=True,
                speculative_draft_model="draft-qwen",
                speculative_draft_max_tokens=4,
                speculative_draft_min_tokens=0,
                speculative_draft_min_continue_probability=0.75,
            )
            store.add_run(run)
            row = store.get_all()[0]
            assert row["speculative_draft_mtp"] in (1, True)
            assert row["speculative_draft_model"] == "draft-qwen"
            assert row["speculative_draft_max_tokens"] == 4
            assert row["speculative_draft_min_continue_probability"] == 0.75
            # Active MTP with a recorded draft model is still canonical.
            assert classify_run_for_result(row) == "canonical"

    def test_mtp_param_sets_produce_different_fingerprints(self):
        a = _base_run(speculative_draft_mtp=True, speculative_draft_model="d",
                      speculative_draft_max_tokens=4,
                      speculative_draft_min_continue_probability=0.75)
        b = _base_run(speculative_draft_mtp=True, speculative_draft_model="d",
                      speculative_draft_max_tokens=3,
                      speculative_draft_min_continue_probability=0)
        assert compute_configuration_fingerprint(a) != compute_configuration_fingerprint(b)


# ---------------------------------------------------------------------------
# Fingerprint properties
# ---------------------------------------------------------------------------
class TestFingerprint:
    def test_deterministic_for_identical_config(self):
        r = _base_run()
        assert compute_configuration_fingerprint(r) == compute_configuration_fingerprint(dict(r))

    def test_one_material_change_changes_fingerprint(self):
        base = _base_run()
        for changed in (
            {"model_quantization": "Q8_0"},
            {"loaded_context": 131072},
            {"flash_attention": False},
            {"eval_batch_size": 4096},
        ):
            other = _base_run(**changed)
            assert compute_configuration_fingerprint(base) != \
                compute_configuration_fingerprint(other), changed

    def test_transient_instance_id_does_not_change_fingerprint(self):
        base = _base_run()
        fp_base = compute_configuration_fingerprint(base)
        # Mutable display name + an arbitrary extra key are NOT material identity.
        assert compute_configuration_fingerprint(_base_run(model_display_name="Different Name")) == fp_base
        assert compute_configuration_fingerprint({**base, "transient_instance_id": "xyz"}) == fp_base


# ---------------------------------------------------------------------------
# Canonical vs incomplete classification
# ---------------------------------------------------------------------------
class TestClassification:
    def test_complete_configuration_is_canonical(self):
        assert classify_run_for_result(_base_run()) == "canonical"

    def test_missing_required_kv_value_is_incomplete(self):
        run = _base_run(**{"kv_cache_k_quantization": None, "kv_cache_v_quantization": None})
        assert classify_run_for_result(run) == "incomplete"

    def test_missing_loaded_context_is_incomplete(self):
        run = _base_run(loaded_context=None)
        assert classify_run_for_result(run) == "incomplete"

    def test_incomplete_result_still_persists_normally(self):
        for csv_p, db_p in _temp_paths():
            store = ResultsStore(csv_path=csv_p, db_path=db_p)
            store.add_run(_base_run(**{"kv_cache_k_quantization": None,
                                       "kv_cache_v_quantization": None}))
            rows = store.get_all()
            assert len(rows) == 1                       # persisted normally
            assert rows[0]["result_classification"] == "incomplete"


# ---------------------------------------------------------------------------
# Isolation: these tests must add ZERO rows to the real active dataset.
# ---------------------------------------------------------------------------
class TestIsolation:
    def test_active_csv_file_untouched_by_temp_store(self):
        import hashlib

        csv_path = Path("data/benchmark_results.csv")
        if not csv_path.exists():
            return  # nothing to protect
        before_lines = len(csv_path.read_text(encoding="utf-8").splitlines())
        before_md5 = hashlib.md5(csv_path.read_bytes()).hexdigest()
        try:
            for csv_p, db_p in _temp_paths():
                store = ResultsStore(csv_path=csv_p, db_path=db_p)
                store.add_run(_base_run(run_id="should-never-leak"))
            after_lines = len(csv_path.read_text(encoding="utf-8").splitlines())
            after_md5 = hashlib.md5(csv_path.read_bytes()).hexdigest()
        finally:
            pass
        assert after_lines == before_lines, "temp-store test wrote into the active CSV!"
        assert after_md5 == before_md5, "temp-store test mutated the active CSV bytes!"
