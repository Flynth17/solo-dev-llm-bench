"""Safe in-app launcher for the locked Context benchmark family (RM-26-AA-0009).

This module is *execution plumbing only*. Its job is to start the locked Context
runner as a separate OS process and report its lifecycle. It does **not** perform
any benchmark logic -- no corpus, scoring, context sizing or reasoning lives here;
all of that belongs to the locked :mod:`src.v2_context_suite_runner`.

Design rules (mirroring :mod:`src.v2_workflow_runner`):

* **Separate OS process.** A Context run can take many minutes (each point sizes a
  large prompt). Running ``run_context_suite`` inside FastAPI's event loop would
  hold the request worker hostage, so we ``subprocess.Popen`` a fresh
  ``python -m src context-suite`` process with an argv list (no shell, no string
  interpolation of untrusted input).

* **One shared run identity.** The run_id is generated server-side using the same
  convention as the runner/artifact (``"ctx-" + os.urandom(6).hex()``) and passed to
  ``context-suite --run-id``. That id becomes the persisted artifact name in
  ``data/context_runs/<run_id>.json``, so there is a single identity for both
  tracking and the durable result.

* **Durable completion signal.** The runner writes its authoritative run document
  atomically once it finishes (see :mod:`src.v2_context_artifact`). Completion is
  therefore proven by ``try_load(run_id)`` returning a valid artifact -- no
  database, no distributed queue.

* **One shared standard-run guard.** Registering here lets the Standard Speed and
  Workflow suites reject a Context launch while one is active (and vice versa) --
  three heavyweight suites must never contend for the same local model.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional

# Read-only artifact loader only -- this never imports or invokes any benchmark
# executor/builder/scorer/corpus path. ``try_load`` reads an already-persisted
# document; it cannot change benchmark execution semantics.
from src.v2_context_artifact import try_load

import src.standard_run_guard as standard_run_guard  # noqa: E402


def _current_runs_dir() -> Path:
    """Return the live Context artifact runs directory (resolved at call time)."""
    import src.v2_context_artifact as _art

    return _art.CONTEXT_RUNS_DIR


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ContextLaunchError(Exception):
    """Pre-launch validation / concurrency failure (mapped to 4xx / 409 by the route)."""


def _current_runs_dir_path() -> Path:
    return _current_runs_dir()


class _ContextRegistry:
    """Tracks only *live* context child processes (no durable state)."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def add(self, run_id: str, proc: subprocess.Popen) -> None:
        with self._lock:
            self._jobs[run_id] = {"proc": proc}

    def get(self, run_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._jobs.get(run_id)

    def active_ids(self) -> list[str]:
        with self._lock:
            return [rid for rid, job in self._jobs.items() if job["proc"].poll() is None]

    def remove_if_idle(self, run_id: str) -> None:
        job = self.get(run_id)
        if job is not None and job["proc"].poll() is not None:
            with self._lock:
                self._jobs.pop(run_id, None)


registry = _ContextRegistry()


def generate_context_run_id() -> str:
    """Generate a server-owned run id matching the persisted-artifact naming."""
    return "ctx-" + os.urandom(6).hex()


def _resolve_lm_studio_url(lm_studio_url: Optional[str]) -> str:
    if lm_studio_url and isinstance(lm_studio_url, str) and lm_studio_url.strip():
        return lm_studio_url.strip().rstrip("/")
    from src.config_loader import load_config

    return (load_config().get("lm_studio_url") or "http://localhost:1234").rstrip("/")


def launch_context(
    model: Optional[str],
    lm_studio_url: Optional[str] = None,
    *,
    project_root: Optional[str] = None,
) -> dict[str, Any]:
    """Launch the locked Context runner as a separate OS process and track it.

    Returns ``{"context_run_id": ..., "status": "running"}``. Raises
    :class:`ContextLaunchError` when the request is invalid (missing model) or when
    another standard run is already active (single-run safety).
    """
    if model is None or not isinstance(model, str) or not model.strip():
        raise ContextLaunchError("A model must be selected to launch the Context benchmark.")

    clean_model = model.strip()
    base_url = _resolve_lm_studio_url(lm_studio_url)

    active = registry.active_ids() or standard_run_guard.active_ids()
    if active:
        raise ContextLaunchError(
            f"A standard benchmark is already in progress ({active[0]}). Wait for it to finish."
        )

    run_id = generate_context_run_id()

    root = Path(project_root) if project_root else _PROJECT_ROOT
    runs_dir = _current_runs_dir_path()
    log_path = runs_dir / f"{run_id}.log"
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_path, "w", encoding="utf-8")
    except OSError:
        with contextlib.suppress(OSError):
            log_handle = open(os.devnull, "w", encoding="utf-8")

    cmd = [
        sys.executable,
        "-m",
        "src",
        "context-suite",
        "--model",
        clean_model,
        "--lm-studio-url",
        base_url,
        "--run-id",
        run_id,
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            env=dict(os.environ),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
    except BaseException as exc:  # pragma: no cover - defensive surface of launch errors
        with contextlib.suppress(OSError):
            log_handle.close()
        raise ContextLaunchError(f"Failed to launch the context process: {exc}")

    registry.add(run_id, proc)
    standard_run_guard.register(run_id, proc)
    return {"context_run_id": run_id, "status": "running"}


def context_status(run_id: str) -> tuple[int, dict[str, Any]]:
    """Return ``(http_status, body)`` for a Context run id.

    Resolution order mirrors the durability contract (mirrors Act 16/17):

    1. Live child handle present and still running -> ``200 running``
    2. Live child handle exited -> completed (if artifact present) else failed
    3. No live handle, durable artifact present -> ``200 completed`` (+ result_url)
    4. No live handle, no artifact -> ``404 not found``
    5. Unsafe/malformed run id -> ``400 invalid run_id``
    """
    try:
        from src.v2_context_artifact import _validate_run_id

        _validate_run_id(run_id)
    except ValueError:
        return 400, {"error": "Invalid run_id"}

    job = registry.get(run_id)
    if job is not None:
        proc = job["proc"]
        exit_code = proc.poll()
        if exit_code is None:
            if try_load(run_id) is not None:
                return 200, _completed_body(run_id)
            return 200, {"context_run_id": run_id, "status": "running"}

        registry.remove_if_idle(run_id)
        if exit_code == 0 and try_load(run_id) is not None:
            return 200, _completed_body(run_id)
        return 200, {
            "context_run_id": run_id,
            "status": "failed",
            "error": _concise_failure(exit_code),
        }

    if try_load(run_id) is not None:
        return 200, _completed_body(run_id)
    return 404, {"error": f"Context run '{run_id}' not found"}


def _concise_failure(exit_code: Optional[int]) -> str:
    if exit_code is None:
        return "Context process exited without producing a result artifact"
    return f"Context process exited with status {exit_code}; check the run log for details"


def _completed_body(run_id: str) -> dict[str, Any]:
    return {
        "context_run_id": run_id,
        "status": "completed",
        "result_url": f"/context/results/{run_id}",
    }
