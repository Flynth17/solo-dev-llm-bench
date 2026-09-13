"""Results storage for Solo Dev LLM Bench.

In-memory list of benchmark runs with SQLite persistence and CSV
migration/compatibility.
Old JSON results.json (if it exists) is left untouched.
"""

import csv
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
_DEFAULT_CSV_PATH = _DEFAULT_DATA_DIR / "benchmark_results.csv"
_DEFAULT_DB_PATH = _DEFAULT_DATA_DIR / "benchmark_results.db"

# CSV column headers
CSV_HEADERS = [
    "timestamp",
    "run_id",
    "model_key",
    "model_display_name",
    "model_quantization",
    "hardware_label",
    "execution_environment",
    "connection_type",
    "iteration",
    "cold_or_warm",
    "tokens_per_second",
    "ttft_seconds",
    "input_tokens",
    "output_tokens",
    "model_load_time_seconds",
    "wall_time_seconds",
    "prompt_name",
    "max_output_tokens",
    "temperature",
    # --- Act 7: reproducible/comparable run metadata (additive) ---
    # Context (kept distinct — never conflated with one another).
    "model_max_context",
    "loaded_context",
    "prompt_tokens",
    # Machine / environment snapshot (stable fields; None when unavailable).
    "cpu_model",
    "cpu_logical_cores",
    "cpu_physical_cores",
    "installed_ram_bytes",
    "gpu_model",
    "total_vram_bytes",
    "os_platform",
    "os_version",
    "nvidia_driver_version",
    "python_version",
    # Invocation-level timing (distinct from per-request wall_time_seconds).
    "benchmark_duration_seconds",
    # --- Act 8: runtime utilisation telemetry (additive) ---
    # System RAM in use.
    "system_ram_used_start_bytes",
    "system_ram_used_peak_bytes",
    "system_ram_used_end_bytes",
    # Current-process RSS.
    "process_rss_start_bytes",
    "process_rss_peak_bytes",
    "process_rss_end_bytes",
    # GPU VRAM in use (distinct from total_vram_bytes captured in Act 7).
    "vram_used_start_bytes",
    "vram_used_peak_bytes",
    "vram_used_end_bytes",
    # CPU / GPU utilisation.
    "cpu_util_avg_pct",
    "cpu_util_peak_pct",
    "gpu_util_avg_pct",
    "gpu_util_peak_pct",
    "telemetry_sample_count",
    # --- Act 11B: V2 inference-configuration identity (additive) ---
    # loaded_context already exists from Act 7 and stays distinct from
    # model_max_context; the V2 fingerprint uses it, so it is re-referenced here.
    "reasoning_mode",
    "flash_attention",
    "offload_kv_cache_to_gpu",
    "eval_batch_size",
    "physical_batch_size",
    "parallel",
    "num_experts",
    "speculative_draft_mtp",
    "speculative_draft_simple",
    "speculative_draft_model",
    "speculative_draft_max_tokens",
    "speculative_draft_min_tokens",
    "speculative_draft_min_continue_probability",
    "kv_cache_k_quantization",
    "kv_cache_v_quantization",
    "kv_cache_quantization_source",
    "lmstudio_instance_config_json",
    # Deterministic V2 fingerprint + canonical/incomplete classification. These are
    # DERIVED at write time and are intentionally kept OUT of the ALTER-migrated
    # OPTIONAL_METADATA set (see _init_db derived-column handling).
    "configuration_fingerprint",
    "result_classification",
]

# SQLite column types matching CSV_HEADERS
SQLITE_COLUMNS = [
    ("timestamp", "TEXT"),
    ("run_id", "TEXT"),
    ("model_key", "TEXT"),
    ("model_display_name", "TEXT"),
    ("model_quantization", "TEXT"),
    ("hardware_label", "TEXT"),
    ("execution_environment", "TEXT"),
    ("connection_type", "TEXT"),
    ("iteration", "INTEGER"),
    ("cold_or_warm", "TEXT"),
    ("tokens_per_second", "REAL"),
    ("ttft_seconds", "REAL"),
    ("input_tokens", "INTEGER"),
    ("output_tokens", "INTEGER"),
    ("model_load_time_seconds", "REAL"),
    ("wall_time_seconds", "REAL"),
    ("prompt_name", "TEXT"),
    ("max_output_tokens", "INTEGER"),
    ("temperature", "REAL"),
    # --- Act 7: additive metadata columns (types mirror CSV_HEADERS order) ---
    ("model_max_context", "INTEGER"),
    ("loaded_context", "INTEGER"),
    ("prompt_tokens", "INTEGER"),
    ("cpu_model", "TEXT"),
    ("cpu_logical_cores", "INTEGER"),
    ("cpu_physical_cores", "INTEGER"),
    ("installed_ram_bytes", "INTEGER"),
    ("gpu_model", "TEXT"),
    ("total_vram_bytes", "INTEGER"),
    ("os_platform", "TEXT"),
    ("os_version", "TEXT"),
    ("nvidia_driver_version", "TEXT"),
    ("python_version", "TEXT"),
    # Invocation-level timing (distinct from per-request wall_time_seconds).
    ("benchmark_duration_seconds", "REAL"),
    # --- Act 8: runtime utilisation telemetry (additive) ---
    ("system_ram_used_start_bytes", "INTEGER"),
    ("system_ram_used_peak_bytes", "INTEGER"),
    ("system_ram_used_end_bytes", "INTEGER"),
    ("process_rss_start_bytes", "INTEGER"),
    ("process_rss_peak_bytes", "INTEGER"),
    ("process_rss_end_bytes", "INTEGER"),
    ("vram_used_start_bytes", "INTEGER"),
    ("vram_used_peak_bytes", "INTEGER"),
    ("vram_used_end_bytes", "INTEGER"),
    ("cpu_util_avg_pct", "REAL"),
    ("cpu_util_peak_pct", "REAL"),
    ("gpu_util_avg_pct", "REAL"),
    ("gpu_util_peak_pct", "REAL"),
    ("telemetry_sample_count", "INTEGER"),
    # --- Act 11B: V2 inference-configuration identity (additive) ---
    ("reasoning_mode", "TEXT"),
    ("flash_attention", "INTEGER"),
    ("offload_kv_cache_to_gpu", "INTEGER"),
    ("eval_batch_size", "INTEGER"),
    ("physical_batch_size", "INTEGER"),
    ("parallel", "INTEGER"),
    ("num_experts", "INTEGER"),
    ("speculative_draft_mtp", "INTEGER"),
    ("speculative_draft_simple", "INTEGER"),
    ("speculative_draft_model", "TEXT"),
    ("speculative_draft_max_tokens", "INTEGER"),
    ("speculative_draft_min_tokens", "INTEGER"),
    ("speculative_draft_min_continue_probability", "REAL"),
    ("kv_cache_k_quantization", "TEXT"),
    ("kv_cache_v_quantization", "TEXT"),
    ("kv_cache_quantization_source", "TEXT"),
    ("lmstudio_instance_config_json", "TEXT"),
    ("configuration_fingerprint", "TEXT"),
    ("result_classification", "TEXT"),
]

# Columns added additively after initial schema creation. Existing databases
# created before these fields gain them via ALTER TABLE so historical rows load
# as NULL/None rather than failing. Values default to blank/None (unavailable).
OPTIONAL_METADATA_COLUMNS = [
    ("model_max_context", "INTEGER"),
    ("loaded_context", "INTEGER"),
    ("prompt_tokens", "INTEGER"),
    ("cpu_model", "TEXT"),
    ("cpu_logical_cores", "INTEGER"),
    ("cpu_physical_cores", "INTEGER"),
    ("installed_ram_bytes", "INTEGER"),
    ("gpu_model", "TEXT"),
    ("total_vram_bytes", "INTEGER"),
    ("os_platform", "TEXT"),
    ("os_version", "TEXT"),
    ("nvidia_driver_version", "TEXT"),
    ("python_version", "TEXT"),
    # Invocation-level timing (distinct from per-request wall_time_seconds).
    ("benchmark_duration_seconds", "REAL"),
    # --- Act 8: runtime utilisation telemetry (additive) ---
    ("system_ram_used_start_bytes", "INTEGER"),
    ("system_ram_used_peak_bytes", "INTEGER"),
    ("system_ram_used_end_bytes", "INTEGER"),
    ("process_rss_start_bytes", "INTEGER"),
    ("process_rss_peak_bytes", "INTEGER"),
    ("process_rss_end_bytes", "INTEGER"),
    ("vram_used_start_bytes", "INTEGER"),
    ("vram_used_peak_bytes", "INTEGER"),
    ("vram_used_end_bytes", "INTEGER"),
    ("cpu_util_avg_pct", "REAL"),
    ("cpu_util_peak_pct", "REAL"),
    ("gpu_util_avg_pct", "REAL"),
    ("gpu_util_peak_pct", "REAL"),
    ("telemetry_sample_count", "INTEGER"),
    # --- Act 11B: V2 inference-configuration identity (additive, ALTER-migrated) ---
    # Ordinary metadata columns: None/blank when absent — never fabricated. Kept
    # OUT of these two derived columns so historical-compatibility tests that assert
    # "every OPTIONAL_METADATA column is None/blank for a bare run" still hold.
    ("reasoning_mode", "TEXT"),
    ("flash_attention", "INTEGER"),
    ("offload_kv_cache_to_gpu", "INTEGER"),
    ("eval_batch_size", "INTEGER"),
    ("physical_batch_size", "INTEGER"),
    ("parallel", "INTEGER"),
    ("num_experts", "INTEGER"),
    ("speculative_draft_mtp", "INTEGER"),
    ("speculative_draft_simple", "INTEGER"),
    ("speculative_draft_model", "TEXT"),
    ("speculative_draft_max_tokens", "INTEGER"),
    ("speculative_draft_min_tokens", "INTEGER"),
    ("speculative_draft_min_continue_probability", "REAL"),
    ("kv_cache_k_quantization", "TEXT"),
    ("kv_cache_v_quantization", "TEXT"),
    ("kv_cache_quantization_source", "TEXT"),
    ("lmstudio_instance_config_json", "TEXT"),
]

# Blank placeholder for missing values
_BLANK = ""

# Migration tracking: simple flag to indicate CSV has been imported
_MIGRATION_FLAG_KEY = "csv_migrated"


def _blank_or(value):
    """Return blank string for None/empty values."""
    if value is None:
        return _BLANK
    return str(value)


# ----------------------------------------------------------------------
# Reporting / presentation helpers (pure, no I/O).
#
# These derive *display-ready* values from stored rows WITHOUT mutating the
# canonical byte/token fields.  Missing data is preserved as None/blank and is
# never synthesised to zero: a missing metric is not equivalent to zero usage.
# ----------------------------------------------------------------------

_BYTES_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def format_bytes(value):
    """Human-readable byte string for presentation.

    Contract (missing data is never faked as zero):
        None  -> None   (hardware/source unavailable)
        ""    -> ""      (blank/unavailable preserved)
        0     -> "0 B"   (a genuine, measured zero)
        other -> KiB/MiB/GiB/TiB with one decimal place

    Non-numeric or negative input is treated as unavailable (``None``) rather
    than a fabricated number.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return ""
    try:
        size = float(int(value))
    except (ValueError, TypeError):
        return None
    if size < 0:
        # Negative is not a valid byte count -> unavailable.
        return None
    if size == 0:
        return "0 B"
    units_index = 0
    while size >= 1024 and units_index < len(_BYTES_UNITS) - 1:
        size /= 1024.0
        units_index += 1
    return f"{size:.1f} {_BYTES_UNITS[units_index]}"


def context_utilisation_pct(row):
    """Derive live-context utilisation percentage from a stored row.

    Context is kept distinct from cumulative token counters: the numerator is
    ``prompt_tokens`` (actual live input tokens for the request) and the
    denominator is the largest available capacity among
    ``model_max_context`` / ``loaded_context``.  Returns None whenever a valid
    denominator does not exist, so callers never divide by zero and never
    fabricate a percentage.
    """
    if not isinstance(row, dict):
        return None

    denom = None
    for key in ("model_max_context", "loaded_context"):
        cap = row.get(key)
        try:
            cap_val = float(int(cap))
        except (ValueError, TypeError):
            continue
        if cap_val > 0 and (denom is None or cap_val > denom):
            denom = cap_val

    prompt_tokens = row.get("prompt_tokens")
    try:
        num = float(int(prompt_tokens))
    except (ValueError, TypeError):
        return None
    if num <= 0 or not denom:
        # No live context to measure against -> not calculable.
        return None

    return round(100.0 * num / denom, 2)


def _comparison_bytes(row, key):
    """Return (canonical_bytes_value, human_readable_or_None) for a byte field."""
    raw = row.get(key)
    return raw, format_bytes(raw)


def build_comparison_summary(run):
    """Compact, comparable snapshot of a single benchmark run.

    Mirrors the existing comparison model (model / quantization / context /
    speed / duration / resource utilisation) and augments it with the Act 7
    machine snapshot and Act 8 runtime telemetry so two runs can be told apart
    at a glance.  Canonical byte values are preserved alongside human-readable
    presentation values; every field degrades to None/blank when unavailable.
    """
    if not isinstance(run, dict):
        return {}

    installed_ram_bytes, installed_ram = _comparison_bytes(run, "installed_ram_bytes")
    total_vram_bytes, total_vram = _comparison_bytes(run, "total_vram_bytes")
    peak_vram_bytes, peak_vram = _comparison_bytes(run, "vram_used_peak_bytes")
    system_ram_peak_bytes, system_ram_peak = _comparison_bytes(
        run, "system_ram_used_peak_bytes"
    )
    process_rss_peak_bytes, process_rss_peak = _comparison_bytes(
        run, "process_rss_peak_bytes"
    )

    warm_tps = run.get("tokens_per_second")
    try:
        decode_speed = round(float(warm_tps), 2) if float(warm_tps) > 0 else None
    except (ValueError, TypeError):
        decode_speed = None

    ttft = run.get("ttft_seconds")
    try:
        ttft_val = round(float(ttft), 3) if float(ttft) is not None and float(ttft) >= 0 else None
    except (ValueError, TypeError):
        ttft_val = None

    duration = run.get("benchmark_duration_seconds")
    try:
        duration_val = round(float(duration), 2) if float(duration) > 0 else None
    except (ValueError, TypeError):
        duration_val = None

    return {
        # Identity / configuration
        "model": run.get("model_display_name") or run.get("model_key") or "",
        "model_key": run.get("model_key") or "",
        "model_quantization": run.get("model_quantization") or "",
        "hardware_label": run.get("hardware_label") or "",
        # Context (kept distinct from cumulative counters)
        "context_size": _largest_capacity(run),
        "loaded_context": run.get("loaded_context"),
        "prompt_tokens": run.get("prompt_tokens"),
        "context_utilisation_pct": context_utilisation_pct(run),
        # Timing (historical TTFT field name preserved)
        "ttft_seconds": ttft_val,
        "tokens_per_second": decode_speed,
        "benchmark_duration_seconds": duration_val,
        # Resource peaks
        "peak_system_ram_bytes": system_ram_peak_bytes,
        "peak_system_ram_human_readable": system_ram_peak,
        "peak_process_rss_bytes": process_rss_peak_bytes,
        "peak_process_rss_human_readable": process_rss_peak,
        "peak_vram_bytes": peak_vram_bytes,
        "peak_vram_human_readable": peak_vram,
        # Runtime utilisation telemetry
        "cpu_util_avg_pct": run.get("cpu_util_avg_pct"),
        "cpu_util_peak_pct": run.get("cpu_util_peak_pct"),
        "gpu_util_avg_pct": run.get("gpu_util_avg_pct"),
        "gpu_util_peak_pct": run.get("gpu_util_peak_pct"),
        "telemetry_sample_count": run.get("telemetry_sample_count"),
        # Static machine snapshot
        "cpu_model": run.get("cpu_model") or "",
        "cpu_logical_cores": run.get("cpu_logical_cores"),
        "cpu_physical_cores": run.get("cpu_physical_cores"),
        "installed_ram_bytes": installed_ram_bytes,
        "installed_ram_human_readable": installed_ram,
        "gpu_model": run.get("gpu_model") or "",
        "total_vram_bytes": total_vram_bytes,
        "total_vram_human_readable": total_vram,
        "nvidia_driver_version": run.get("nvidia_driver_version") or "",
        "os_platform": run.get("os_platform") or "",
        "os_version": run.get("os_version") or "",
        "python_version": run.get("python_version") or "",
    }


def _largest_capacity(run):
    """Return the largest available context capacity (max of known capacities)."""
    if not isinstance(run, dict):
        return None
    best = None
    for key in ("model_max_context", "loaded_context"):
        cap = run.get(key)
        try:
            cap_val = int(cap)
        except (ValueError, TypeError):
            continue
        if cap_val > 0 and (best is None or cap_val > best):
            best = cap_val
    return best


def enriched_run(row):
    """Return a presentation-ready copy of a stored run for reporting.

    The canonical byte/token fields are preserved untouched; display-only keys
    (human-readable RAM/VRAM, derived context utilisation and a compact
    comparison summary) are added.  This never mutates the input row or the
    persisted store.
    """
    if not isinstance(row, dict):
        return {}

    enriched = dict(row)
    installed_ram_bytes = row.get("installed_ram_bytes")
    total_vram_bytes = row.get("total_vram_bytes")
    peak_vram_bytes = row.get("vram_used_peak_bytes")
    system_ram_peak_bytes = row.get("system_ram_used_peak_bytes")

    enriched["installed_ram"] = format_bytes(installed_ram_bytes)
    enriched["total_vram"] = format_bytes(total_vram_bytes)
    enriched["peak_vram"] = format_bytes(peak_vram_bytes)
    enriched["peak_system_ram"] = format_bytes(system_ram_peak_bytes)
    enriched["context_utilisation_pct"] = context_utilisation_pct(row)
    enriched["comparison_summary"] = build_comparison_summary(enriched)
    return enriched


# ----------------------------------------------------------------------
# Act 11B: V2 configuration identity — fingerprint / classification / label.
#
# A BenchLLM V2 result identifies the MODEL + INFERENCE CONFIGURATION, not merely
# the model name. These helpers are pure (no I/O) so they can be unit tested in
# isolation and reused by both the persistence layer and reporting helpers.
# ----------------------------------------------------------------------

# Canonical reasoning setting for benchmark requests. Mirrors the value sent in
# src/benchmark.py's request payload so what we persist is exactly what ran.
BENCHMARK_REASONING_MODE = "off"

# Labels that indicate a *failed* quantization lookup rather than a real weight
# format. A run whose quantization resolves to one of these does NOT have a known
# weight quantization and must not be treated as canonical on that basis alone.
_QUANT_NOT_REAL_LABELS = frozenset({
    "metadata_absent",
    "metadata_malformed",
    "model_not_found",
    "lookup_failed",
})

# First-class, material inference-configuration keys used to build the fingerprint.
# Order is fixed and serialization sorts keys so output is stable regardless of how
# a row was constructed. Transient instance IDs / TTLs are deliberately excluded.
_FINGERPRINT_KEYS = (
    "model_key",
    "model_quantization",
    "loaded_context",
    # Quality output-budget policy: makes old 4096 / old 90% / new 75% runs
    # distinguishable configurations even when every other setting is identical.
    "output_budget_policy",
    "reasoning_mode",
    "kv_cache_k_quantization",
    "kv_cache_v_quantization",
    "flash_attention",
    "offload_kv_cache_to_gpu",
    "eval_batch_size",
    "physical_batch_size",
    "parallel",
    "num_experts",
    "speculative_draft_mtp",
    "speculative_draft_simple",
    "speculative_draft_model",
    "speculative_draft_max_tokens",
    "speculative_draft_min_tokens",
    "speculative_draft_min_continue_probability",
)

# Configuration fields that must be *known* for a run to qualify as canonical. If
# any required value is missing/None/blank the run is stored as `incomplete`.
_REQUIRED_FOR_CANONICAL = (
    "model_key",
    "model_quantization",
    "loaded_context",
    "reasoning_mode",
    "kv_cache_k_quantization",
    "kv_cache_v_quantization",
    "flash_attention",
    "offload_kv_cache_to_gpu",
    "eval_batch_size",
    "physical_batch_size",
    "parallel",
)

# Static/hardware identity fields: at least one must be present for canonical.
_HARDWARE_IDENTITY_KEYS = ("cpu_model", "installed_ram_bytes", "gpu_model")


def _known(value):
    """True when a value is meaningfully present (not None/blank)."""
    return value is not None and value != ""


def normalize_loaded_instance_config(instance: dict) -> dict:
    """Normalize an LM Studio loaded-instance object into first-class fields.

    ``instance`` is a per-model ``loaded_instances[]`` member, i.e.
    ``{"id": ..., "config": {...}, "remaining_ttl_seconds": ...}``. The returned dict
    carries the material inference-configuration settings plus a deterministic JSON
    representation of the useful subset (transient id / TTL excluded). KV cache
    quantization is not exposed by the registry, so it is recorded as ``unknown``
    rather than fabricated. ``reasoning_mode`` reflects the canonical benchmark rule
    (explicitly off) — see :data:`BENCHMARK_REASONING_MODE`.
    """
    cfg = {} if not isinstance(instance, dict) else (instance.get("config") or {})

    def _bool(key):
        val = cfg.get(key)
        return bool(val) if isinstance(val, (bool, int, float)) else False

    def _int(key):
        val = cfg.get(key)
        return val if isinstance(val, int) and not isinstance(val, bool) else None

    def _str(key):
        val = cfg.get(key)
        return val if isinstance(val, str) else ""

    out = {
        "loaded_context": _int("context_length"),
        "reasoning_mode": BENCHMARK_REASONING_MODE,
        "flash_attention": _bool("flash_attention"),
        "offload_kv_cache_to_gpu": _bool("offload_kv_cache_to_gpu"),
        "eval_batch_size": _int("eval_batch_size"),
        "physical_batch_size": _int("physical_batch_size"),
        "parallel": _int("parallel"),
        "num_experts": _int("num_experts"),
        "speculative_draft_mtp": _bool("speculative_draft_mtp"),
        "speculative_draft_simple": _bool("speculative_draft_simple"),
        "speculative_draft_model": _str("speculative_draft_model"),
        "speculative_draft_max_tokens": _int("speculative_draft_max_tokens"),
        "speculative_draft_min_tokens": _int("speculative_draft_min_tokens"),
        "speculative_draft_min_continue_probability": cfg.get(
            "speculative_draft_min_continue_probability"
        ),
        # KV cache quantization is not exposed by the registry — record unknown,
        # never infer it from memory usage / model type / missing fields.
        "kv_cache_k_quantization": None,
        "kv_cache_v_quantization": None,
        "kv_cache_quantization_source": "unknown",
        # Deterministic representation of the useful settings subset (id/TTL excluded).
        "lmstudio_instance_config_json": json.dumps(
            cfg, sort_keys=True, separators=(",", ":"), default=str
        ),
    }
    return out


def compute_configuration_fingerprint(run: dict) -> str:
    """Deterministic SHA-256 fingerprint over a run's material config.

    Same complete configuration => same fingerprint; changing one material setting
    yields a different fingerprint. Uses model identity + weight quantization (not
    mutable display names). Returns ``""`` when there is no model identity to key on.
    """
    if not isinstance(run, dict):
        return ""
    model_key = run.get("model_key")
    if not _known(model_key):
        return ""

    subset = {}
    for key in _FINGERPRINT_KEYS:
        value = run.get(key)
        # Normalize Python bools to ints so the serialized form is stable.
        if isinstance(value, bool):
            value = 1 if value else 0
        subset[key] = value

    blob = json.dumps(subset, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def classify_run_for_result(run: dict) -> str:
    """Classify a persisted run as ``"canonical"`` or ``"incomplete"``.

    A run is canonical only when every material experiment-defining setting needed to
    reproduce it is known (model identity + quantization, loaded context, reasoning
    mode, K/V cache values, flash attention, KV GPU offload, batch config, parallel
    value, hardware identity). MTP/speculative state must be present; if MTP is active
    a draft model must also be recorded. Anything unknown => ``"incomplete"`` — the run
    still persists normally, it is merely not an official leaderboard entry.
    """
    if not isinstance(run, dict):
        return "incomplete"

    def _present(key):
        return _known(run.get(key))

    # Model identity + a real weight quantization (not a failure-mode label).
    if not _present("model_key"):
        return "incomplete"
    quant = run.get("model_quantization")
    if not _known(quant) or quant in _QUANT_NOT_REAL_LABELS:
        return "incomplete"

    # loaded_context must be a genuine positive capacity.
    ctx = run.get("loaded_context")
    if not (isinstance(ctx, int) and not isinstance(ctx, bool) and ctx > 0):
        return "incomplete"

    # Batch config must be genuinely configured (> 0).
    for key in ("eval_batch_size", "physical_batch_size", "parallel"):
        val = run.get(key)
        if not (isinstance(val, int) and not isinstance(val, bool) and val > 0):
            return "incomplete"

    # Remaining required material settings present.
    for key in _REQUIRED_FOR_CANONICAL:
        if not _present(key):
            return "incomplete"

    # MTP/speculative state where applicable — an active MTP draft must be recorded.
    if run.get("speculative_draft_mtp") and not _known(run.get("speculative_draft_model")):
        return "incomplete"

    # Hardware identity already captured by existing telemetry/static metadata.
    if not any(_present(key) for key in _HARDWARE_IDENTITY_KEYS):
        return "incomplete"

    return "canonical"


def build_readable_config_label(run: dict) -> str:
    """Concise, human-readable configuration label (derived helper only).

    Example: ``"Qwen 3.8 27B Q5_K_M \u2014 MTP ON"``. Full details are available
    separately via :func:`readable_config_details`; this never encodes every obscure
    setting into one giant string. The fingerprint remains the canonical identity —
    this label is display-only.
    """
    if not isinstance(run, dict):
        return ""

    name = run.get("model_display_name") or run.get("model_key") or ""
    quant = run.get("model_quantization") or ""
    head = f"{name} {quant}".strip() if (name and quant) else (name or "")

    mtp_on = bool(run.get("speculative_draft_mtp"))
    return head + " \u2014 MTP ON" if mtp_on else head + " \u2014 MTP OFF"


def readable_config_details(run: dict) -> dict:
    """Structured, detailed view of a run's configuration (display helper only)."""
    if not isinstance(run, dict):
        return {}

    loaded = run.get("loaded_context")
    max_ctx = run.get("model_max_context")
    mtp_on = bool(run.get("speculative_draft_mtp"))
    ctx_str = (
        f"CTX {int(loaded)}{f' / MAX {int(max_ctx)}' if _known(str(max_ctx)) else ''}"
        if isinstance(loaded, int) and loaded > 0
        else "CTX unknown"
    )
    mtp_str = (
        f"{int(run['speculative_draft_max_tokens'])}/"
        f"{int(run['speculative_draft_min_tokens'])}/"
        f"{run.get('speculative_draft_min_continue_probability', 0)}"
        if mtp_on
        else "OFF"
    )
    return {
        "context": ctx_str,
        "k_cache": run.get("kv_cache_k_quantization") or "unknown",
        "v_cache": run.get("kv_cache_v_quantization") or "unknown",
        "reasoning": (run.get("reasoning_mode") or "unknown").upper(),
        "mtp": mtp_str,
    }


class ResultsStore:
    """Manages benchmark results in memory and on disk.

    SQLite is the primary persistence layer.  CSV is preserved for
    backward-compatibility and migration purposes.
    """

    def __init__(
        self,
        csv_path: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.csv_path = csv_path or _DEFAULT_CSV_PATH
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.runs: list[dict] = []

        # Ensure directories exist
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure CSV header exists
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_HEADERS)

        # Initialise SQLite database
        self._init_db()

        # Run CSV migration if needed
        self._migrate_csv_to_sqlite()

        # Load from SQLite (source of truth)
        self._load_from_db()

    # ------------------------------------------------------------------
    # SQLite helpers
    # ------------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        """Return a sqlite3 connection with row_factory set."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        """Create the runs table if it does not exist."""
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    run_id TEXT,
                    model_key TEXT,
                    model_display_name TEXT,
                    model_quantization TEXT,
                    hardware_label TEXT,
                    execution_environment TEXT,
                    connection_type TEXT,
                    iteration INTEGER,
                    cold_or_warm TEXT,
                    tokens_per_second REAL,
                    ttft_seconds REAL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    model_load_time_seconds REAL,
                    wall_time_seconds REAL,
                    prompt_name TEXT,
                    max_output_tokens INTEGER,
                    temperature REAL
                )
            """)
            # Ensure the model_quantization column exists on databases created before this field.
            cols_cursor = conn.execute("PRAGMA table_info(runs)")
            existing_cols = {row[1] for row in cols_cursor.fetchall()}
            if "model_quantization" not in existing_cols:
                conn.execute(
                    "ALTER TABLE runs ADD COLUMN model_quantization TEXT DEFAULT ''"
                )
            # Additive, backward-compatible metadata columns (Act 7). Databases
            # created before these fields gain them via ALTER so historical rows
            # simply load as NULL/None instead of crashing.
            for _col, _type in OPTIONAL_METADATA_COLUMNS:
                if _col not in existing_cols:
                    conn.execute(
                        f"ALTER TABLE runs ADD COLUMN {_col} {_type} DEFAULT ''"
                    )
            # Determined V2 fingerprint + classification columns (Act 11B). These are
            # derived at write time; ensure they exist on any database (fresh or
            # pre-existing) via ALTER, mirroring the additive pattern. They are kept out
            # of OPTIONAL_METADATA_COLUMNS so historical-compatibility tests that assert
            # "every OPTIONAL_METADATA column is None/blank for a bare run" still hold.
            for _col, _type in (("configuration_fingerprint", "TEXT"),
                                ("result_classification", "TEXT")):
                if _col not in existing_cols:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {_col} {_type}")
            conn.commit()
            # Metadata table for migration tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert a sqlite3.Row to a plain dict with type conversion."""
        if row is None:
            return {}
        result = {}
        for key in row.keys():
            value = row[key]
            # NULL in SQLite -> None
            if value is None:
                result[key] = None
            else:
                result[key] = value
        return result

    def _load_from_db(self) -> None:
        """Load all runs from SQLite into memory."""
        self.runs = []
        conn = self._get_connection()
        try:
            cursor = conn.execute("SELECT * FROM runs ORDER BY id ASC")
            for row in cursor:
                self.runs.append(self._row_to_dict(row))
        finally:
            conn.close()

    def _dict_to_values(self, run: dict) -> tuple:
        """Convert a dict to a tuple matching SQLITE_COLUMNS order."""
        values = []
        for col in CSV_HEADERS:
            v = run.get(col)
            if v is None or v == _BLANK:
                values.append(None)
            else:
                values.append(v)
        return tuple(values)

    # ------------------------------------------------------------------
    # CSV migration
    # ------------------------------------------------------------------

    def _migrate_csv_to_sqlite(self) -> None:
        """Import existing CSV rows into SQLite if not already migrated.

        Migration is idempotent: it uses a boolean flag stored in the
        metadata table.  Once the flag is set, the CSV is never read
        for import again.  New benchmark runs write to both SQLite
        (primary) and CSV (compatibility mirror).
        """
        # Check whether migration already happened
        migrated = self._get_migration_flag()
        if migrated:
            return

        # Count CSV data rows (excluding header)
        csv_row_count = 0
        if self.csv_path.exists():
            with open(self.csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for _ in reader:
                    csv_row_count += 1

        if csv_row_count == 0:
            # No CSV data to import — still mark as migrated.
            self._set_migration_flag(True)
            return

        # Import all CSV rows into SQLite.
        conn = self._get_connection()
        try:
            with open(self.csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    values = self._dict_to_values(row)
                    conn.execute(
                        f"INSERT INTO runs ({', '.join(CSV_HEADERS)}) "
                        f"VALUES ({','.join(['?'] * len(CSV_HEADERS))})",
                        values,
                    )
            conn.commit()
            # Mark migration as complete.
            self._set_migration_flag(True)
        finally:
            conn.close()

    def _get_migration_flag(self) -> bool:
        """Return True if CSV migration has already been performed."""
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT value FROM metadata WHERE key = ?",
                (_MIGRATION_FLAG_KEY,),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            return row["value"] == "1"
        finally:
            conn.close()

    def _set_migration_flag(self, value: bool) -> None:
        """Record that CSV migration has been performed."""
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (_MIGRATION_FLAG_KEY, "1" if value else "0"),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Public API (compatible with the old interface)
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Legacy alias — now a no-op since loading happens in __init__."""
        pass

    @staticmethod
    def _parse_row(row: dict) -> dict:
        """Legacy CSV parser — kept for backward compatibility."""
        result = {}
        for key, value in row.items():
            if value == _BLANK or value is None:
                result[key] = None
                continue
            try:
                if "." in value:
                    result[key] = float(value)
                else:
                    result[key] = int(value)
            except ValueError:
                result[key] = value
        return result

    def save(self) -> None:
        """Persist all in-memory runs to SQLite (and CSV for compatibility)."""
        # Write to CSV for backward-compatibility
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)
            for run in self.runs:
                writer.writerow([run.get(h, _BLANK) for h in CSV_HEADERS])

        # Write to SQLite: replace all rows
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM runs")
            for run in self.runs:
                values = self._dict_to_values(run)
                conn.execute(
                    f"INSERT INTO runs ({', '.join(CSV_HEADERS)}) "
                    f"VALUES ({','.join(['?'] * len(CSV_HEADERS))})",
                    values,
                )
            conn.commit()
        finally:
            conn.close()

    def add_run(self, run_data: dict) -> None:
        """Append a single benchmark iteration and persist.

        Derives + persists ``configuration_fingerprint`` and
        ``result_classification`` from whatever material configuration fields the row
        already carries (see :func:`compute_configuration_fingerprint` /
        :func:`classify_run_for_result`). Never mutates the caller's dict.
        """
        run = dict(run_data)
        run["configuration_fingerprint"] = compute_configuration_fingerprint(run)
        run["result_classification"] = classify_run_for_result(run)
        self.runs.append(run)
        self.save()

    def get_all(self) -> list[dict]:
        """Return all saved benchmark runs (source of truth)."""
        # Reload from SQLite to ensure freshness
        self._load_from_db()
        return self.runs

    def clear(self) -> None:
        """Clear all results from memory, SQLite, and CSV."""
        self.runs = []
        self.save()

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_run(self, run_id: str) -> bool:
        """Delete all rows for a given run_id from SQLite and memory.

        Uses a transaction for safety.  Returns True if any rows were
        removed, False if the run_id was not found.
        """
        conn = self._get_connection()
        try:
            # Count rows before deletion (for return value)
            cursor = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE run_id = ?",
                (run_id,),
            )
            count = cursor.fetchone()[0]
            if count == 0:
                return False

            # Delete rows in a transaction
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            conn.commit()
        finally:
            conn.close()

        # Remove from in-memory list
        self.runs = [r for r in self.runs if r.get("run_id") != run_id]
        return True
