"""Runtime resource utilisation telemetry for a single benchmark invocation (Act 8).

Act 7 records *static* machine capability (installed RAM, GPU model, core counts,
...). Act 8 measures what is actually *consumed* while one benchmark runs: system
RAM, current-process RSS, GPU VRAM and CPU/GPU utilisation.

Measurement lifecycle (one ``run_id`` / invocation)::

    capture start metrics -> start sampler -> run all iterations
        -> stop sampler -> compute avg/peak -> capture end metrics
        -> attach invocation telemetry to rows

The same invocation-level values are attached to every result row produced by the
invocation, mirroring how :data:`run_id`, the static hardware snapshot and
``benchmark_duration_seconds`` are shared per invocation. No new service or table is
created; the values are plain serialisable fields persisted exactly like Act 7.

Design constraints (mirror Act 7):

- Standard library only (+ optional ``nvidia-smi`` / PowerShell subprocess when present).
  No new dependency is added — see Phase 1 audit.
- Every metric degrades to ``None`` ("unavailable") rather than being fabricated or
  guessed; a missing/failed source never crashes the run.
- The sampler runs in a short-lived daemon thread and never blocks the benchmark loop,
  so it does not distort the request metrics being measured (``tokens_per_second``,
  ``wall_time_seconds``).
- Sensible sampling interval (~1s) to keep background overhead low. A small number of
  trustworthy samples is preferable to measurement-induced distortion.

GPU selection caveat: with multiple NVIDIA GPUs we monitor the lowest-index GPU
(index 0) — the same implicit choice Act 7 makes for its capability snapshot. We do
not claim a metric belongs to a specific model's inference GPU across multiple GPUs;
on a single-GPU machine (the common case) this is exact.
"""

import os
import platform
import re
import shutil
import subprocess
import threading
import time

# Default cadence, inside the intended 0.5-1.0s window. Kept at ~1s to minimise the
# background cost of spawning ``nvidia-smi`` / PowerShell on each tick; a daemon thread
# performs this work so it does not add to measured request wall-clock time.
TELEMETRY_SAMPLE_INTERVAL = 1.0


def _safe_int(value) -> int | None:
    """Coerce *value* to int, returning None on any failure."""
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _leading_int(value) -> int | None:
    """Extract the first integer from a string that may carry a unit suffix,
    e.g. '16384 kB' -> 16384. Returns None if no digit is found."""
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


# ----------------------------------------------------------------------
# Per-source collectors — all best-effort; all return None (or a dict of Nones) on failure.
# ----------------------------------------------------------------------

def read_system_ram_used_bytes() -> int | None:
    """System RAM currently in use (total - available), cross-platform."""
    system = platform.system()
    try:
        if system == "Windows":
            # Win32_OperatingSystem reports sizes in kB. used = total - free.
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "$o=Get-CimInstance Win32_OperatingSystem;"
                 "'{0} {1}' -f $o.TotalVisibleMemorySize, $o.FreePhysicalMemory"],
                capture_output=True, text=True, timeout=15,
            )
            if out.returncode == 0 and out.stdout.strip():
                parts = out.stdout.split()
                if len(parts) >= 2:
                    total = _safe_int(parts[0])
                    free = _safe_int(parts[1])
                    if total is not None and free is not None and total > 0:
                        used = total - free
                        return used * 1024 if used > 0 else None
            return None
        if system == "Linux":
            # /proc/meminfo values are in kB. used = MemTotal - MemAvailable.
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                totals = memavail = None
                for line in f:
                    if line.startswith("MemTotal:"):
                        totals = _leading_int(line.split(":", 1)[1])
                    elif line.startswith("MemAvailable:"):
                        memavail = _leading_int(line.split(":", 1)[1])
                    if totals is not None and memavail is not None:
                        break
            if totals is not None and memavail is not None:
                used = totals - memavail
                return used * 1024 if used > 0 else None
            return None
        # Darwin / other: no reliable dependency-free path implemented -> unavailable.
        return None
    except Exception:
        return None


def read_process_rss_bytes() -> int | None:
    """Current resident set size (RSS) of this process, cross-platform."""
    system = platform.system()
    try:
        if system == "Windows":
            # WorkingSet64 yields current RSS in bytes. Pin the PID explicitly so the
            # child shell cannot resolve a different $pid.
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"(Get-Process -Id {os.getpid()}).WorkingSet64"],
                capture_output=True, text=True, timeout=15,
            )
            if out.returncode == 0 and out.stdout.strip():
                rss = _safe_int(out.stdout.split()[0])
                return rss if rss and rss > 0 else None
            return None
        if system == "Linux":
            # /proc/self/statm field 2 (resident) in pages.
            page_size = os.sysconf("SC_PAGE_SIZE")
            with open("/proc/self/statm", "r", encoding="utf-8") as f:
                resident_pages = _safe_int(f.read().split()[1])
            if resident_pages is not None and page_size > 0:
                return resident_pages * page_size
            return None
        # Darwin / other: no reliable dependency-free path implemented -> unavailable.
        return None
    except Exception:
        return None


def _nvidia_gpu_metrics(index: int) -> dict[str, object]:
    """Return ``{"vram_used_bytes": int|None, "gpu_util_pct": float|None}`` for one GPU.

    Uses ``nvidia-smi`` when available on PATH. Both values degrade to None if the tool
    is absent or fails — never fabricated. Memory.used is reported in MiB.
    """
    exe = shutil.which("nvidia-smi")
    result: dict[str, object] = {"vram_used_bytes": None, "gpu_util_pct": None}
    if not exe:
        return result
    try:
        out = subprocess.run(
            [exe, "--query-gpu=index,memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return result

        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
        # nvidia-smi may print a leading "Warning" line; skip until we see our index.
        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3 or parts[0] == "Warning":
                continue
            idx = _safe_int(parts[0])
            if idx != index:
                continue
            mib = _safe_int(parts[1])
            if mib is not None and mib > 0:
                result["vram_used_bytes"] = mib * 1024 * 1024
            util = parts[2]
            try:
                value = float(util)
            except ValueError:
                value = None
            if isinstance(value, (int, float)):
                result["gpu_util_pct"] = value
            break
        return result
    except Exception:
        return result


def read_system_cpu_cumulative() -> dict[str, int] | None:
    """Cumulative system CPU counters since boot, for delta-based utilisation.

    Returns a normalised {"total": ..., "idle": ...} pair (both cumulative counts) so
    :func:`cpu_util_pct_from_delta` can compute utilisation uniformly across the Windows
    {kernel+user, idle} layout and the Linux /proc/stat jiffie layout. Returns None if
    unavailable.
    """
    system = platform.system()
    try:
        if system == "Windows":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            idle = ctypes.c_uint64()
            kernel = ctypes.c_uint64()
            user = ctypes.c_uint64()
            ok = bool(kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)))
            if not ok:
                return None
            # Normalised shape: total (busy+idle) and idle in 100ns ticks. On Windows
            # lpKernelTime already includes idle, so total = kernel + user.
            return {"total": kernel.value + user.value, "idle": idle.value}
        if system == "Linux":
            fields = ("user", "nice", "system", "idle", "iowait", "irq",
                      "softirq", "steal")
            with open("/proc/stat", "r", encoding="utf-8") as f:
                first = f.readline().split()
            if not first or first[0] != "cpu":
                return None
            values = [_safe_int(v) for v in first[1:]]
            if len(values) < len(fields):
                return None
            counters = dict(zip(fields, values))
            idle_all = counters.get("idle", 0) + counters.get("iowait", 0)
            # Normalised shape: total jiffies across all fields and idle (incl. iowait).
            return {"total": sum(counters.values()), "idle": idle_all}
        return None
    except Exception:
        return None


def cpu_util_pct_from_delta(
    prev: dict[str, int], cur: dict[str, int] | None,
) -> float | None:
    """CPU utilisation (%) over the interval between *prev* and *cur* cumulative counts.

    Both arguments carry a normalised {"total", "idle"} shape, so the same formula is
    correct for Windows (kernel time includes idle) and Linux /proc/stat::

        util% = 100 * (delta_total - delta_idle) / delta_total
    """
    if not prev or cur is None:
        return None
    try:
        # Both dicts carry a normalised {total, idle} shape (see
        # read_system_cpu_cumulative), so this formula is correct for both the
        # Windows (kernel includes idle) and Linux /proc/stat counter layouts.
        total_delta = cur["total"] - prev["total"]
        idle_delta = cur["idle"] - prev["idle"]
        if total_delta <= 0:
            return None
        util = 100.0 * (total_delta - idle_delta) / total_delta
        # Guard against tiny rounding dips below zero when counters barely move.
        return max(0.0, min(100.0, round(util, 2)))
    except Exception:
        return None


# ----------------------------------------------------------------------
# Sampler
# ----------------------------------------------------------------------

class TelemetrySampler:
    """Collects a snapshot of utilisation metrics at a fixed interval for one invocation.

    A short-lived daemon thread samples every ``sample_interval`` seconds while the
    benchmark runs. Peaks are the maximum instantaneous value observed; CPU/GPU averages
    and peaks are computed over the per-tick utilisation samples on stop(). All collectors
    degrade to None when unavailable.

    The internal :meth:`_sample_tick` is isolated so tests can inject canned values and
    drive the loop deterministically without touching live hardware or real timing.
    """

    def __init__(self, sample_interval: float = TELEMETRY_SAMPLE_INTERVAL, gpu_index: int = 0):
        self.sample_interval = sample_interval
        self.gpu_index = gpu_index
        self._samples: list[dict[str, object]] = []
        self._cpu_samples: list[float | None] = []
        self._gpu_util_samples: list[float | None] = []
        self._start_values: dict[str, int | None] = {}
        self._end_values: dict[str, int | None] = {}
        self._baseline_cpu: dict[str, int] | None = None
        self._baseline_time: float | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Capture invocation-start metrics and begin sampling (spawn daemon thread)."""
        self._start_values = {
            "system_ram_used_bytes": read_system_ram_used_bytes(),
            "process_rss_bytes": read_process_rss_bytes(),
            **_nvidia_gpu_metrics(self.gpu_index),
        }
        # Prime the CPU baseline so the first tick measures a real interval.
        self._baseline_cpu = read_system_cpu_cumulative()
        self._baseline_time = time.perf_counter()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="telemetry-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, object]:
        """Stop sampling, capture end metrics and compute the full telemetry dict.

        Safe to call once; always returns a complete dict (all metric keys present),
        with ``None`` for any value whose source was unavailable.
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

        self._end_values = {
            "system_ram_used_bytes": read_system_ram_used_bytes(),
            "process_rss_bytes": read_process_rss_bytes(),
            **_nvidia_gpu_metrics(self.gpu_index),
        }
        return self._aggregate()

    # -- internals ----------------------------------------------------

    def _sample_tick(self) -> dict[str, object]:
        """Collect one utilisation snapshot. Overridable by tests with canned values."""
        nvidia = _nvidia_gpu_metrics(self.gpu_index)
        cur_cpu = read_system_cpu_cumulative()
        cpu_pct = cpu_util_pct_from_delta(
            self._baseline_cpu, cur_cpu) if self._baseline_cpu else None
        return {
            "system_ram_used_bytes": read_system_ram_used_bytes(),
            "process_rss_bytes": read_process_rss_bytes(),
            "vram_used_bytes": nvidia.get("vram_used_bytes"),
            "gpu_util_pct": nvidia.get("gpu_util_pct"),
            "cpu_util_pct": cpu_pct,
        }

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            tick = self._sample_tick()
            self._samples.append(tick)
            self._cpu_samples.append(tick.get("cpu_util_pct"))
            self._gpu_util_samples.append(tick.get("gpu_util_pct"))

            now = time.perf_counter()
            cur_cpu = read_system_cpu_cumulative()
            if cur_cpu is not None:
                self._baseline_cpu = cur_cpu
                self._baseline_time = now
            # Wait up to one interval; wakes immediately when stop() is requested.
            self._stop_event.wait(self.sample_interval)

    def _max_or_none(self, values) -> object:
        usable = [v for v in values if isinstance(v, (int, float))]
        return max(usable) if usable else None

    def _avg_or_none(self, values) -> object:
        usable = [v for v in values if isinstance(v, (int, float))]
        return round(sum(usable) / len(usable), 2) if usable else None

    def _aggregate(self) -> dict[str, object]:
        ram_peaks = [s.get("system_ram_used_bytes") for s in self._samples]
        rss_peaks = [s.get("process_rss_bytes") for s in self._samples]
        vram_peaks = [s.get("vram_used_bytes") for s in self._samples]

        return {
            # RAM
            "system_ram_used_start_bytes": self._start_values.get("system_ram_used_bytes"),
            "system_ram_used_peak_bytes": self._max_or_none(ram_peaks),
            "system_ram_used_end_bytes": self._end_values.get("system_ram_used_bytes"),
            # Process RSS
            "process_rss_start_bytes": self._start_values.get("process_rss_bytes"),
            "process_rss_peak_bytes": self._max_or_none(rss_peaks),
            "process_rss_end_bytes": self._end_values.get("process_rss_bytes"),
            # GPU VRAM
            "vram_used_start_bytes": self._start_values.get("vram_used_bytes"),
            "vram_used_peak_bytes": self._max_or_none(vram_peaks),
            "vram_used_end_bytes": self._end_values.get("vram_used_bytes"),
            # CPU / GPU utilisation
            "cpu_util_avg_pct": self._avg_or_none(self._cpu_samples),
            "cpu_util_peak_pct": self._max_or_none(self._cpu_samples),
            "gpu_util_avg_pct": self._avg_or_none(self._gpu_util_samples),
            "gpu_util_peak_pct": self._max_or_none(self._gpu_util_samples),
            # Interpretability aid for very short invocations.
            "telemetry_sample_count": len(self._samples),
        }
