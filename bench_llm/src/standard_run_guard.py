"""Shared, in-memory concurrency guard for *standard* benchmark runs (Act 17).

A "standard run" is any heavyweight benchmark that speaks to the local model and
therefore must never overlap another standard run -- concurrent GPU activity would
corrupt timing measurements. Both the Standard Speed Suite (this Act) and the locked
Workflow Suite (Act 16) register their launched OS-process handle here so the two
suites conflict *bidirectionally*:

    Workflow running -> Speed launch is rejected (409)
    Speed      running -> Workflow launch is rejected (409)

Design constraints honoured:

* **Smallest safe mechanism, not a scheduler.** This is one process-handle set plus
  a lock. There is no queue, no persistence, no reaper thread -- lifecycle state is
  pruned lazily on every access via the child's ``.poll()`` (returns an exit code
  once the process has ended).

* **No false "still running" after exit.** An entry whose handle reports a finished
  exit code is dropped on the next prune, so a completed run never blocks a later
  launch forever.

* **FastAPI restart loses state by design.** This matches the Act 16 contract: after
  a restart we cannot know about pre-restart OS processes, so in-memory state simply
  resets. Completion is otherwise proven durably (Workflow artifact / Speed rows).
"""

from __future__ import annotations

import threading
from typing import Optional

# run_id -> child process handle (anything exposing ``.poll()``; a Popen or test fake).
_ACTIVE: dict[str, object] = {}
# run_id -> final exit code, recorded once when a child exits. Lets status lookups tell
# "exited non-zero with no result" (failed) apart from "never tracked / post-restart"
# (None), without retaining live handles past their useful life.
_EXITED: dict[str, int] = {}
_LOCK = threading.RLock()


def _prune_locked() -> None:
    """Drop entries whose child has exited, recording each exit code. Holder: :data:`_LOCK`."""
    exited = {rid: proc for rid, proc in _ACTIVE.items() if proc.poll() is not None}
    for rid, proc in exited.items():
        try:
            _EXITED[rid] = int(proc.poll())
        except (TypeError, ValueError):
            pass
        del _ACTIVE[rid]
    live = {rid: proc for rid, proc in _ACTIVE.items()}
    # Only reassign when something changed to keep the dict identity stable.
    if len(live) != len(_ACTIVE):
        _ACTIVE.clear()
        _ACTIVE.update(live)


def register(run_id: str, proc: object) -> None:
    """Track a launched standard run's child handle under *run_id*."""
    with _LOCK:
        _prune_locked()
        _ACTIVE[run_id] = proc


def unregister(run_id: str) -> None:
    """Stop tracking *run_id* (e.g. on launch failure)."""
    with _LOCK:
        _ACTIVE.pop(run_id, None)
        _EXITED.pop(run_id, None)


def exit_code(run_id: str) -> Optional[int]:
    """Return the recorded final exit code for *run_id* (or ``None`` if untracked)."""
    with _LOCK:
        return _EXITED.get(run_id)


def active_ids() -> list[str]:
    """Return the ids of standard runs whose child process is still alive."""
    with _LOCK:
        _prune_locked()
        return list(_ACTIVE.keys())


def is_standard_run_active(exclude: Optional[str] = None) -> Optional[str]:
    """Return an active standard-run id other than *exclude*, or ``None``.

    Used by launchers to reject a new run while another standard run is in flight.
    """
    with _LOCK:
        _prune_locked()
        for rid in _ACTIVE.keys():
            if exclude is None or rid != exclude:
                return rid
    return None
