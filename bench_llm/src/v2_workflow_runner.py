"""Safe in-app launcher for the locked V2 Workflow quality suite (Act 16).

This module is *execution plumbing only*. Its job is to start the existing,
locked workflow runner as a separate OS process and report its lifecycle. It does
**not** perform any benchmark logic: no corpus, validators, prompts, reasoning or
output-budget policy live here -- all of that belongs to the locked suite runner.

Execution authority (never superseded, never re-implemented):

    python -m src v2-run   ->   src.v2_quality_suite_runner.run_v2_run

Explicitly **forbidden** (this module must neither import nor call it):

    src.v2_quality_executor::run_v2_quality_live

Design rules
------------
* **Separate OS process.** A Workflow run can take many minutes and the locked
  runner already spawns five isolated suite subprocesses itself. Running
  ``run_v2_run`` *inside* FastAPI's event loop would hold the request worker
  hostage for the whole benchmark, so we instead ``subprocess.Popen`` a fresh
  ``python -m src v2-run`` process with an argv list (no shell, no string
  interpolation of untrusted input).

* **One shared run identity.** The run_id is generated server-side using the same
  convention as the runner itself (``"run-" + os.urandom(6).hex()``) and passed to
  ``v2-run --run-id``. That id becomes the persisted artifact name in
  ``data/v2_runs/<run_id>.json``, so there is a single identity for both tracking
  and the durable result.

* **Durable completion signal.** The runner writes its authoritative run document
  atomically once it finishes (see :mod:`src.v2_quality_artifact`). Completion is
  therefore proven by ``try_load(run_id)`` returning a valid artifact -- no database,
  no distributed queue. If FastAPI restarts and loses the in-memory process handle,
  an already-finished run is still reported as completed via that same artifact.

* **Small in-memory registry.** Tracks only live child handles so we can tell
  ``queued/running`` from ``failed``. Exited entries are pruned on next poll; they
  never masquerade as "still running" after a restart.
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
# executor/validator/corpus path. ``try_load`` reads an already-persisted document;
# it cannot change benchmark execution semantics and never touches the legacy
# executor (its import graph reaches only the immutable canonical constants).
from src.v2_quality_artifact import try_load

# Shared cross-suite concurrency guard (Act 17). Registering here lets the Standard
# Speed Suite reject its launch while a Workflow run is active, and vice versa -- two
# heavyweight standard suites must never contend for the same local model.
import src.standard_run_guard as standard_run_guard  # noqa: E402


def _current_runs_dir() -> Path:
    """Return the live artifact runs directory.

    Resolved through the :mod:`src.v2_quality_artifact` module at call time so a
    test that monkeypatches ``art.V2_RUNS_DIR`` (as the existing V2 route tests do)
    is honoured without this module holding a stale reference.
    """
    import src.v2_quality_artifact as _art

    return _art.V2_RUNS_DIR


# Project root -- identical to where the locked suite runner spawns its child
# ``python -m src v2-suite`` processes, so ``-m src`` resolves on ``sys.path``.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class WorkflowLaunchError(Exception):
    """Pre-launch validation / concurrency failure (mapped to 4xx / 409).

    Carries a concise, human-safe message (never a stack trace or internal detail).
    """


def _concise_failure(exit_code: Optional[int]) -> str:
    """Return a short, safe reason for a finished-but-unsuccessful workflow run.

    Avoids dumping logs or stack traces. Exit code ``2`` is the locked runner's
    ``ConfigurationMismatchError`` signal (e.g. expected model not loaded); other
    non-zero codes are reported generically.
    """
    if exit_code == 2:
        return "Workflow configuration mismatch (expected model not loaded or incompatible settings)"
    if exit_code is None:
        return "Workflow process exited without producing a result artifact"
    return f"Workflow process exited with status {exit_code}; check the run log for details"


# ---------------------------------------------------------------------------
# Small in-memory registry of live child processes.
# ---------------------------------------------------------------------------

class _WorkflowRegistry:
    """Tracks only *live* workflow child processes (no durable state)."""

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
        """Return ids whose child is still running (``poll()`` has not reaped it)."""
        with self._lock:
            return [rid for rid, job in self._jobs.items() if job["proc"].poll() is None]

    def remove_if_idle(self, run_id: str) -> None:
        """Drop an exited entry so the registry cannot grow without bound."""
        job = self.get(run_id)
        if job is not None and job["proc"].poll() is not None:
            with self._lock:
                self._jobs.pop(run_id, None)


# Exactly one shared registry for the process (mirrors ``app_state.results_store``).
registry = _WorkflowRegistry()


def generate_run_id() -> str:
    """Generate a server-owned run id matching the persisted-artifact naming.

    The runner uses exactly this convention when it persists its result, so the id
    handed back to the UI is the *same* id stored at ``data/v2_runs/<run_id>.json``.
    """
    return "run-" + os.urandom(6).hex()


def _resolve_lm_studio_url(lm_studio_url: Optional[str]) -> str:
    """Return a clean LM Studio base URL, falling back to the configured default."""
    if lm_studio_url and isinstance(lm_studio_url, str) and lm_studio_url.strip():
        return lm_studio_url.strip().rstrip("/")
    from src.config_loader import load_config

    return (load_config().get("lm_studio_url") or "http://localhost:1234").rstrip("/")


def launch_workflow(
    model: Optional[str],
    lm_studio_url: Optional[str] = None,
    *,
    project_root: Optional[str] = None,
) -> dict[str, Any]:
    """Launch the locked workflow runner as a separate OS process and track it.

    Returns ``{"run_id": ..., "status": "running"}``. Raises
    :class:`WorkflowLaunchError` when the request is invalid (missing model) or when
    another Workflow run is already active (single-run safety).
    """
    # --- Validate the selected model through the same semantics the runner expects. ---
    if model is None or not isinstance(model, str) or not model.strip():
        raise WorkflowLaunchError("A model must be selected to launch the Workflow suite.")

    clean_model = model.strip()
    base_url = _resolve_lm_studio_url(lm_studio_url)

    # --- Single standard run at a time, enforced across BOTH suites via the shared
    #     guard so Workflow<->Speed conflict bidirectionally (GPU contention would
    #     corrupt timing measurements). ---
    active = registry.active_ids() or standard_run_guard.active_ids()
    if active:
        raise WorkflowLaunchError(
            f"A standard benchmark is already in progress ({active[0]}). Wait for it to finish."
        )

    run_id = generate_run_id()

    # --- Stream child stdout/stderr to a bounded local log file. Never capture into
    #     memory (a multi-minute benchmark could otherwise balloon FastAPI's heap). ---
    root = Path(project_root) if project_root else _PROJECT_ROOT
    runs_dir = _current_runs_dir()
    log_path = runs_dir / f"{run_id}.log"

    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_path, "w", encoding="utf-8")
    except OSError:
        # Dev-safe fallback: if the data dir is not writable, discard output rather
        # than aborting a valid launch. Status is still derivable from exit code/artifact.
        log_handle = open(os.devnull, "w", encoding="utf-8")

    # --- argv list only; no shell, no untrusted interpolation into command strings. ---
    cmd = [
        sys.executable,
        "-m",
        "src",
        "v2-run",
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
        raise WorkflowLaunchError(f"Failed to launch the workflow process: {exc}")

    registry.add(run_id, proc)
    # Publish into the shared guard so the Speed suite sees this active handle too.
    standard_run_guard.register(run_id, proc)
    return {"run_id": run_id, "status": "running"}


def workflow_status(run_id: str) -> tuple[int, dict[str, Any]]:
    """Return ``(http_status, body)`` for a Workflow run id.

    Resolution order mirrors the durability contract:

    1. Live child handle present and still running   -> ``200 running``
    2. Live child handle exited                      -> completed (if artifact present) else failed
    3. No live handle, durable artifact present       -> ``200 completed`` (+ result_url)
    4. No live handle, no artifact                    -> ``404 not found``
    5. Unsafe/malformed run id                        -> ``400 invalid run_id``

    Never pretends a lost process is still running after a restart: without a live
    handle and without an artifact the run is unknown (404).
    """
    # Reject unsafe ids before any filesystem interaction with them.
    try:
        from src.v2_quality_artifact import _validate_run_id

        _validate_run_id(run_id)
    except ValueError:
        return 400, {"error": "Invalid run_id"}

    job = registry.get(run_id)
    if job is not None:
        proc = job["proc"]
        exit_code = proc.poll()
        if exit_code is None:
            # Still running -- but a finished artifact (e.g. after a restart) wins.
            if try_load(run_id) is not None:
                return 200, _completed_body(run_id)
            return 200, {"run_id": run_id, "status": "running"}

        # Child has exited; drop the stale handle so it stops being tracked.
        registry.remove_if_idle(run_id)
        if exit_code == 0 and try_load(run_id) is not None:
            return 200, _completed_body(run_id)
        return 200, {"run_id": run_id, "status": "failed", "error": _concise_failure(exit_code)}

    # No live handle (possibly pre-restart). Durable artifact is the source of truth.
    if try_load(run_id) is not None:
        return 200, _completed_body(run_id)
    return 404, {"error": f"Workflow run '{run_id}' not found"}


def _completed_body(run_id: str) -> dict[str, Any]:
    """Canonical completed-run payload with the read-only result link."""
    return {
        "run_id": run_id,
        "status": "completed",
        "result_url": f"/v2/results/{run_id}",
    }
