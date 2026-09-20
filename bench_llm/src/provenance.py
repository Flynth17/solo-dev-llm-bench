"""Run-level execution provenance for Solo Dev LLM Bench.

A single, versioned, **shared** contract that captures the immutable environment +
configuration which produced a benchmark run's evidence. The guiding rules:

* **Capture ONCE at benchmark start.** Provenance is a point-in-time snapshot of the
  runtime configuration *at the moment the benchmark begins*, not a re-derivation from
  later telemetry or the current machine.
* **Store at the RUN level, never per request/suite/stage.** Child evidence inherits
  provenance through its ``run_id``; hardware/runtime/model/inference fields are NOT
  duplicated into every suite/request record.
* **One contract across families.** Speed (flat store), Workflow and Context (JSON
  artifacts) all attach the *same* structure -- there are no three incompatible
  hardware schemas.
* **Three meanings, never collapsed.**

  =============================  ================================================
  Meaning                        Representation
  =============================  ================================================
  ``NOT STORED``                 No provenance was persisted for this run (a legacy
                                 artifact / row). Absence of the object entirely.
  ``UNKNOWN AT EXECUTION``       Provenance exists but the runtime did not expose the
                                 field at execution time. Explicit ``"unknown"`` value.
  ``CURRENT VALUE``              A real, captured value (e.g. quantization ``Q4_K_M``,
                                 a CPU model name).
  =============================  ================================================

These are deliberately distinct: a legacy run reports ``NOT STORED`` for *every*
provenance field; a modern run with an unexposed field reports
``UNKNOWN AT EXECUTION`` for that field only; anything else is a ``CURRENT VALUE``.

Design constraints honoured by this module:

* **Capture-only, no benchmark logic.** It never imports or invokes any executor,
  validator, corpus, LM Studio transport or subprocess benchmark path. Host capture
  uses the Python standard library only (no ``psutil`` / third-party deps) so it can
  run anywhere BenchLLM runs. GPU metadata is best-effort via ``nvidia-smi`` and degrades
  to "unavailable" rather than being guessed.
* **Never fabricates.** Every field carries a real value or an explicit sentinel; there
  is no inference of hardware/model details from bare model names.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Identity / versioning
# ---------------------------------------------------------------------------

# Schema version of the *provenance structure*. Bump only when the on-disk shape
# evolves; older provenance objects keep their own version and are read as-is.
PROVENANCE_SCHEMA_VERSION: int = 1

ARTIFACT_TYPE_PROVENANCE: str = "solo-dev-llm-bench.provenance"

# ---------------------------------------------------------------------------
# The three distinct field meanings (see module docstring).
# ---------------------------------------------------------------------------

#: Meaning of an *absent* provenance object / absent field on a legacy run.
NOT_STORED: str = "not_stored"

#: Explicit sentinel stored when the runtime did not expose a field at execution
#: time. Distinct from ``None`` (absence) and from any real value. This is what a
#: read-model maps to "UNKNOWN AT EXECUTION".
UNKNOWN_EXECUTION: str = "unknown"


# ---------------------------------------------------------------------------
# Small normalisation helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (with timezone)."""
    return datetime.now(timezone.utc).isoformat()


def clean(value: Any) -> Optional[str]:
    """Return a trimmed non-empty string, or ``None`` for None / blank / whitespace.

    Real scalars that are not strings (ints, floats, bools) pass through unchanged so
    numeric provenance such as byte counts and core counts is preserved exactly.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _bool_or_unknown(value: Any) -> Optional[bool]:
    """Coerce a truthy LM Studio flag to ``bool``; unknown/absent -> ``UNKNOWN_EXECUTION``.

    A genuine ``False`` is preserved (it is a real, meaningful value); only absent or
    non-boolean values degrade to the explicit "unknown" sentinel.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return UNKNOWN_EXECUTION


# ---------------------------------------------------------------------------
# Host capture (HOST source -- standard library only, no third-party deps).
# ---------------------------------------------------------------------------

def _cpu_model() -> Optional[str]:
    """Best-effort human CPU model label.

    On Windows the central-processor registry key holds the authoritative label; on
    other platforms ``platform.processor()`` is used. Returns ``None`` when neither
    yields a usable value (recorded as ``UNKNOWN_EXECUTION``, never guessed).
    """
    try:  # Windows: authoritative human-readable processor name.
        import winreg  # type: ignore  # only available on Windows

        key = winreg.OpenKey(
            winreg.HARDWARE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        )
        name, _type = winreg.QueryValueEx(key, "Processor Name")
        result = clean(name)
        if result:
            return result
    except Exception:
        pass
    return clean(platform.processor())


def _system_ram_bytes() -> Optional[int]:
    """Return total physical RAM in bytes (HOST source, standard library only).

    ``psutil`` is not a dependency, so this uses the Windows ``GlobalMemoryStatusEx``
    entry point via :mod:`ctypes`. On non-Windows hosts it falls back to reading
    ``/proc/memtotal`` (Linux) and otherwise returns ``None`` -- an honest "unavailable"
    rather than a guessed value.
    """
    if platform.system() == "Windows":
        try:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            class MEMSTATUS(ctypes.Structure):  # local import keeps it scoped
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailable", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailableVirtual", ctypes.c_ulonglong),
                    ("ullAvailableExtendedVirtual", ctypes.c_ulonglong),
                ]
            status = MEMSTATUS()
            status.dwLength = ctypes.sizeof(MEMSTATUS)
            if kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        except Exception:
            pass
        return None
    # Linux: /proc/memtotal reports kB.
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return int(parts[1]) * 1024
    except Exception:
        pass
    return None


def _capture_gpus() -> list[dict[str, Any]]:
    """Return one entry per GPU reported by ``nvidia-smi`` (best-effort).

    When the NVIDIA CLI is not on ``PATH`` (no driver / non-NVIDIA host) this returns an
    empty list -- the absence itself is the honest signal and is surfaced as "unavailable"
    rather than being fabricated. Each entry carries name, VRAM in bytes and driver version.
    """
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return []

    try:
        completed = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except Exception:
        return []

    gpus: list[dict[str, Any]] = []
    for line in (completed.stdout or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        name, vram_mib, driver = parts[0], parts[1], parts[2]
        vram_bytes: Optional[int] = None
        if vram_mib not in ("", "?") and vram_mib.replace(".", "").isdigit():
            vram_bytes = int(float(vram_mib)) * 1024 * 1024
        gpus.append(
            {
                "name": clean(name),
                "vram_bytes": vram_bytes,
                "driver_version": clean(driver),
            }
        )
    return gpus


def capture_hardware() -> dict[str, Any]:
    """Capture the executing machine's *static* hardware (HOST source).

    Transient utilisation / memory-in-use measurements are deliberately excluded -- those
    are runtime telemetry, not immutable provenance. Every field is a real value or an
    explicit ``UNKNOWN_EXECUTION`` sentinel; nothing is inferred from elsewhere.
    """
    ram_bytes = _system_ram_bytes()

    gpus = _capture_gpus()
    return {
        "cpu_model": (_cpu_model() or UNKNOWN_EXECUTION),
        "cpu_architecture": (clean(platform.machine()) or UNKNOWN_EXECUTION),
        "logical_cpu_count": os.cpu_count(),
        "system_ram_bytes": ram_bytes,
        # GPU list is empty when no driver CLI is present; callers distinguish that from
        # a machine with zero GPUs via the explicit ``count`` field below.
        "gpu": gpus,
        "gpu_count": len(gpus),
    }


def capture_runtime(
    *,
    backend_type: str = "lm_studio",
    backend_url: Optional[str] = None,
) -> dict[str, Any]:
    """Capture the runtime/backend environment (HOST + DIRECT source).

    ``backend_type`` / ``backend_url`` are supplied by the caller (DIRECT). LM Studio
    version and inference engine are not exposed by a stable API surface today, so they
    record ``UNKNOWN_EXECUTION`` rather than being scraped from GUI state or guessed.
    """
    os_name = clean(platform.system()) or UNKNOWN_EXECUTION
    os_version = clean(platform.version()) or clean(platform.release()) or UNKNOWN_EXECUTION
    return {
        "backend_type": (clean(backend_type) or UNKNOWN_EXECUTION),
        "backend_url": clean(backend_url),
        # Not exposed by the stable LM Studio registry/load API -- explicit unknown.
        "lm_studio_version": UNKNOWN_EXECUTION,
        "runtime_engine": UNKNOWN_EXECUTION,
        "runtime_engine_version": UNKNOWN_EXECUTION,
        "os": os_name,
        "os_version": os_version,
    }


def _quantization_for_provenance(value: Any) -> Any:
    """Map a resolved weight-quantization value into the provenance model section.

    The *weight* quantization (e.g. ``Q4_K_M``) is distinct from KV-cache quantization
    and must never be confused with it. A resolution failure-mode label (``metadata_absent``,
    ``lookup_failed`` ...) is a real, honest value reported by the runtime -- store it as-is
    rather than collapsing it to "unknown".
    """
    cleaned = clean(value)
    return cleaned if cleaned else UNKNOWN_EXECUTION


def build_model_section(
    model_identifier: Optional[str],
    model_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the ``model`` provenance section from a resolved config (DIRECT source).

    Only fields the runtime actually exposes are populated; everything else is an explicit
    sentinel or ``None``. Publisher / file path / architecture are not part of the current
    LM Studio registry normalization, so they record ``UNKNOWN_EXECUTION`` rather than being
    inferred from the bare identifier.
    """
    cfg = model_config or {}
    return {
        "identifier": clean(model_identifier),
        # Not exposed by fetch_models normalisation -- never inferred from the name.
        "publisher": UNKNOWN_EXECUTION,
        "model_path": None,
        "model_filename": None,
        "architecture": UNKNOWN_EXECUTION,
        # Weight quantization (distinct from KV-cache quantization).
        "quantization": _quantization_for_provenance(cfg.get("model_quantization")),
        "file_size_bytes": None,
        "max_context": cfg.get("model_max_context"),
        "loaded_context": cfg.get("loaded_context"),
    }


def build_inference_section(
    model_config: Optional[dict[str, Any]] = None,
    request_params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the ``inference`` provenance section (DIRECT source).

    KV-cache K/V quantization is **not** exposed by LM Studio's registry, so it records
    ``UNKNOWN_EXECUTION`` -- never the weight quantization. Request-time sampling params
    (temperature / top_p / top_k / seed / max_output_tokens) come from the benchmark's own
    request configuration when available.
    """
    cfg = model_config or {}
    req = request_params or {}

    mtp_active = bool(cfg.get("speculative_draft_mtp"))
    speculative_active = bool(cfg.get("speculative_draft_simple") or mtp_active)

    return {
        # KV-cache quantization is deliberately separate from weight quantization and is
        # unexposed by the runtime -> explicit unknown, never guessed/inferred.
        "kv_cache_k_type": UNKNOWN_EXECUTION,
        "kv_cache_v_type": UNKNOWN_EXECUTION,
        "flash_attention": _bool_or_unknown(cfg.get("flash_attention")),
        "gpu_offload": _bool_or_unknown(cfg.get("offload_kv_cache_to_gpu")),
        "cpu_offload": _bool_or_unknown(cfg.get("cpu_offload")),
        "loaded_context": cfg.get("loaded_context"),
        "reasoning_mode": clean(cfg.get("reasoning_mode")) or UNKNOWN_EXECUTION,
        "speculative_enabled": _bool_or_unknown(speculative_active),
        "draft_model": clean(cfg.get("speculative_draft_model")),
        "mtp_state": (clean(cfg.get("mtp_state")) if cfg.get("mtp_state") else UNKNOWN_EXECUTION),
        "temperature": req.get("temperature"),
        "top_p": req.get("top_p"),
        "top_k": req.get("top_k"),
        "seed": req.get("seed"),
        "max_output_tokens": req.get("max_output_tokens"),
    }


def capture_provenance(
    *,
    lm_studio_url: Optional[str] = None,
    model_identifier: Optional[str] = None,
    model_config: Optional[dict[str, Any]] = None,
    request_params: Optional[dict[str, Any]] = None,
    captured_at: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble one run-level provenance snapshot.

    Call this exactly once at benchmark start and attach the result to the run document
    (Workflow / Context artifacts) or persist it at run level (Speed store). The returned
    object is a plain JSON-native dict so it round-trips unchanged through serialization.
    """
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE_PROVENANCE,
        "captured_at": captured_at or _now_iso(),
        "hardware": capture_hardware(),
        "runtime": capture_runtime(backend_url=lm_studio_url),
        "model": build_model_section(model_identifier, model_config),
        "inference": build_inference_section(model_config, request_params),
    }


# ---------------------------------------------------------------------------
# Read-model helpers: distinguish the three meanings without collapsing them.
# ---------------------------------------------------------------------------

def provenance_present(document: Any) -> bool:
    """Return True when *document* carries a non-empty provenance object."""
    if not isinstance(document, dict):
        return False
    prov = document.get("provenance")
    return isinstance(prov, dict) and bool(prov)


def field_status(value: Any) -> str:
    """Classify a single persisted provenance value into one of the three meanings.

    * ``None``                                   -> :data:`NOT_STORED`
    * ``"unknown"`` (the explicit sentinel)      -> ``"unknown_at_execution"``
    * any other real value                       -> ``"stored"``
    """
    if value is None:
        return NOT_STORED
    if isinstance(value, str) and value == UNKNOWN_EXECUTION:
        return "unknown_at_execution"
    return "stored"


def to_json(provenance: dict[str, Any]) -> str:
    """Serialise a provenance object to a stable JSON string (for flat stores)."""
    return json.dumps(provenance, sort_keys=True, ensure_ascii=False, default=str)


def from_json(payload: Optional[str]) -> Optional[dict[str, Any]]:
    """Parse a provenance JSON string back into a dict; ``None``/blank -> ``None``."""
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None
