"""Stable machine / environment snapshot for Solo Dev LLM Bench (Act 7).

Captures platform-level hardware and OS facts once per benchmark invocation so
runs are reproducible and comparable. Design constraints:

- Standard library only (+ an optional ``nvidia-smi`` subprocess when present).
- No clock telemetry (CPU/GPU clocks, boost clocks) — excluded by design.
- No live RAM/VRAM *utilisation* sampling — that belongs to Act 8. This module
  reports *installed* capacity only.
- Every field is best-effort and degrades to ``None`` ("unavailable") rather
  than being fabricated or guessed. A missing source must never crash the run.

The returned value is a plain ``dict`` of JSON-serialisable primitives
(``str`` / ``int`` / ``None``) so it can be persisted alongside benchmark rows.
"""

import os
import platform
import re
import shutil
import subprocess


def _safe_int(value) -> int | None:
    """Coerce *value* to int, returning None on any failure."""
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# OS / interpreter
# ----------------------------------------------------------------------

def _os_platform() -> str | None:
    """Return a concise OS name, e.g. 'Windows' / 'Linux' / 'Darwin'."""
    try:
        system = platform.system()
        return system or None
    except Exception:
        return None


def _os_version() -> str | None:
    """Return the detailed OS version string (best-effort)."""
    try:
        version = platform.version()
        return version or None
    except Exception:
        return None


def _python_version() -> str | None:
    try:
        return platform.python_version() or None
    except Exception:
        return None


# ----------------------------------------------------------------------
# CPU model + core counts
# ----------------------------------------------------------------------

def _cpu_model() -> str | None:
    """Best-effort human-readable CPU model name, cross-platform."""
    system = platform.system()
    try:
        if system == "Linux":
            with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("Model"):
                        # e.g. "Model\t: AMD Ryzen ..."
                        value = line.split(":", 1)[1].strip()
                        return value or None
            return None
        if system == "Darwin":
            out = subprocess.run(
                ["sysctl", "-n", "hw.model"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0:
                return out.stdout.strip() or None
            return None
        # Windows (and other platforms): platform.processor() yields a model
        # string on Windows; fall back to the generic processor identifier.
        value = platform.processor()
        return value or None
    except Exception:
        return None


def _cpu_logical_cores() -> int | None:
    try:
        count = os.cpu_count()
        return count if count and count > 0 else None
    except Exception:
        return None


def _cpu_physical_cores() -> int | None:
    """Best-effort total physical core count across all sockets (Linux only)."""
    system = platform.system()
    if system != "Linux":
        return None
    try:
        per_socket_cores: dict[str, int] = {}
        current_socket = None
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("processor"):
                    # New logical processor -> close out the previous socket block.
                    current_socket = None
                elif line.startswith("physical id"):
                    current_socket = line.split(":", 1)[1].strip()
                elif line.startswith("cpu cores") and current_socket is not None:
                    cores = _safe_int(line.split(":", 1)[1].strip())
                    if cores is not None:
                        per_socket_cores[current_socket] = cores
        if not per_socket_cores:
            return None
        return sum(per_socket_cores.values())
    except Exception:
        return None


# ----------------------------------------------------------------------
# Installed RAM
# ----------------------------------------------------------------------

def _installed_ram_bytes() -> int | None:
    """Installed physical memory in bytes (no live utilisation sampling)."""
    system = platform.system()
    try:
        if system == "Windows":
            # Dependency-free: query Win32_ComputerSystem via PowerShell.
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                match = re.search(r"(\d+)", result.stdout)
                if match:
                    value = int(match.group(1))
                    return value if value > 0 else None
            return None
        if system == "Linux":
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        # Value is in kB.
                        kb = _safe_int(line.split(":", 1)[1].strip())
                        return kb * 1024 if kb else None
            return None
        if system == "Darwin":
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0:
                return _safe_int(out.stdout.strip())
            return None
        return None
    except Exception:
        return None


# ----------------------------------------------------------------------
# GPU + driver (NVIDIA best-effort via nvidia-smi)
# ----------------------------------------------------------------------

def _nvidia_info() -> tuple[str | None, int | None, str | None]:
    """Return (gpu_model, total_vram_bytes, driver_version) best-effort.

    Uses ``nvidia-smi`` when available on PATH. Returns all-None if the tool is
    absent or fails — never fabricates a value.
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        return (None, None, None)
    try:
        result = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return (None, None, None)

        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        if not lines:
            return (None, None, None)
        # nvidia-smi may print a leading warning line; skip until we see data.
        fields = None
        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3 and parts[0] not in ("", "Warning"):
                fields = parts
                break
        if not fields:
            return (None, None, None)

        gpu_model = fields[0] or None
        driver_version = fields[2] or None
        vram_bytes = None
        # memory.total is reported in MiB.
        mib = _safe_int(fields[1])
        if mib is not None and mib > 0:
            vram_bytes = mib * 1024 * 1024
        return (gpu_model, vram_bytes, driver_version)
    except Exception:
        return (None, None, None)


# ----------------------------------------------------------------------
# Aggregate snapshot
# ----------------------------------------------------------------------

def snapshot_hardware() -> dict[str, object]:
    """Return a stable machine/environment snapshot.

    Every value is ``str`` / ``int`` / ``None`` and the call never raises: any
    source that cannot be read degrades to ``None`` ("unavailable"). Compute this
    once per benchmark invocation and reuse it; do not recompute per token or per
    result row.
    """
    gpu_model, total_vram_bytes, driver_version = _nvidia_info()
    return {
        "cpu_model": _cpu_model(),
        "cpu_logical_cores": _cpu_logical_cores(),
        "cpu_physical_cores": _cpu_physical_cores(),
        "installed_ram_bytes": _installed_ram_bytes(),
        "gpu_model": gpu_model,
        "total_vram_bytes": total_vram_bytes,
        "os_platform": _os_platform(),
        "os_version": _os_version(),
        "nvidia_driver_version": driver_version,
        "python_version": _python_version(),
    }
