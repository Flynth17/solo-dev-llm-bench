"""Production launcher + lifecycle status for the canonical Speed evidence suite.

This is the NEW authoritative launch path for Standard Speed. It spawns a fresh OS process
that runs :func:`src.speed_evidence_runner.run_canonical_speed_suite` (PREFILL + GENERATION
under one run id into one dedicated evidence store) instead of the legacy ``v2-speed``
runner, and resolves lifecycle status by reading that canonical store.

The legacy runner (:mod:`src.v2_speed_suite_runner`) is preserved verbatim for backward
compat -- historical runs still live in the shared ResultsStore and are served here through
an explicit fallback so existing data keeps working. Canonical and legacy runs are told
apart by WHERE their durable rows live (dedicated evidence table vs shared store).

    POST   /api/v2/speed/run                            -> 202 {speed_run_id, status}
    GET    /api/v2/speed/runs/{speed_run_id}/status     -> 200 | 400 | 404

Lifecycle states: ``running`` -> ``completed`` (+ result_url) or ``failed``. Completion is
proven by durable rows in the dedicated evidence store; no database-backed queue is used.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

# Reuse proven helpers from the legacy runner (URL resolution, run-id generation, guard,
# validation). This module only changes WHAT is spawned and WHERE status is read from -- it
# performs no benchmark logic.
from src.v2_speed_suite_runner import (  # noqa: E402
    SpeedLaunchError,
    _PROJECT_ROOT,
    _resolve_lm_studio_url,
    _speed_runs_root,
    _validate_speed_run_id,
    generate_speed_run_id,
)

import src.standard_run_guard as standard_run_guard  # noqa: E402


def launch_canonical_speed(
    model: Optional[str],
    lm_studio_url: Optional[str] = None,
    *,
    hardware_label: str = "",
    project_root: Optional[str] = None,
) -> dict[str, Any]:
    """Launch the canonical Speed suite as a separate OS process and track it.

    Spawns ``python -m src speed-suite`` (runs :func:`run_canonical_speed_suite`) rather than
    the legacy ``v2-speed`` runner. Returns ``{"speed_run_id": ..., "status": "running"}``.
    Raises :class:`SpeedLaunchError` when the request is invalid or another standard run is
    already active -- two heavyweight suites must never contend for the same local model.
    """
    if model is None or not isinstance(model, str) or not model.strip():
        raise SpeedLaunchError("A model must be selected to launch the Speed suite.")

    clean_model = model.strip()
    base_url = _resolve_lm_studio_url(lm_studio_url)

    active = standard_run_guard.active_ids()
    if active:
        raise SpeedLaunchError(
            f"A standard benchmark is already in progress ({active[0]}). Wait for it to finish."
        )

    run_id = generate_speed_run_id()
    root = Path(project_root) if project_root else _PROJECT_ROOT

    # argv list only; no shell, no untrusted interpolation into command strings.
    cmd: list[str] = [
        sys.executable,
        "-m",
        "src",
        "speed-suite",
        "--model",
        clean_model,
        "--lm-studio-url",
        base_url,
        "--run-id",
        run_id,
    ]
    if hardware_label and str(hardware_label).strip():
        cmd += ["--hardware-label", str(hardware_label).strip()]

    runs_dir = _speed_runs_root()
    log_path = runs_dir / f"{run_id}.log"
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_path, "w", encoding="utf-8")
    except OSError:
        with contextlib.suppress(OSError):
            log_handle = open(os.devnull, "w", encoding="utf-8")

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
        raise SpeedLaunchError(f"Failed to launch the speed process: {exc}")

    standard_run_guard.register(run_id, proc)
    return {"speed_run_id": run_id, "status": "running"}


def _canonical_rows_for(speed_run_id: str) -> list[dict[str, Any]]:
    """Return canonical evidence rows for *speed_run_id* (empty when none / store missing)."""
    try:
        from src.speed_evidence_store import SpeedEvidenceStore

        return SpeedEvidenceStore().get_by_run_id(speed_run_id)
    except Exception:  # pragma: no cover - defensive; missing store never crashes status
        return []


def _coerce_capacity(value):
    """Coerce a persisted token count to an int capacity for the eligibility path.

    SQLite REAL affinity stores integer token counts as floats (e.g. ``32768.0``); those must
    be accepted so the capacity gate still fires after a round-trip through the store. A
    non-integral float, string, ``None`` or bool is rejected (returns ``None``) so a malformed
    value can never silently become a valid capacity -- it is treated as "unknown capacity" and
    every point is conservatively skipped rather than guessed. Mirrors the
    ``isinstance(c, int) and not isinstance(c, bool)`` guard in
    :func:`src.speed_evidence_runner.prefill_effective_capacity`.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    return None


def _canonical_suite_complete(rows: list[dict[str, Any]], speed_run_id: str) -> bool:
    """Return True when the suite reached a terminal ``completed`` state.

    Mirrors the orchestrator's authoritative completion rule (see
    :func:`src.speed_evidence_runner.run_canonical_speed_suite`): overall status is
    ``completed`` once *any* point has completed. This function additionally guards against a
    premature ``completed`` on partial persistence by requiring that every EXPECTED point is
    present with a terminal row.

    Expected points are capacity-aware:
      * All three GENERATION context points (``8K / 16K / 32K``) are always attempted, so all
        must be present + terminal. A missing generation point means the child crashed
        mid-run -- NOT completion.
      * PREFILL fixtures that do not fit the resolved effective context window are never
        persisted (the suite skips them), so only prefill fixtures that actually fit are
        expected. This is what lets a reduced-capacity run with partial support still report
        ``completed`` instead of a spurious ``failed``.
    """
    from src.speed_evidence_read_model import load_speed_evidence_result
    from src.speed_fixtures import canonical_fixture_ids, load_fixture
    from src.speed_evidence_runner import (
        EVIDENCE_OUTPUT_TOKENS,
        prefill_effective_capacity,
        prefill_point_eligible,
    )

    try:
        result = load_speed_evidence_result(speed_run_id, rows)
    except Exception:  # pragma: no cover - defensive
        return False

    by_fid = {p.get("fixture_id"): p for p in result.get("points", [])}
    terminal = {"completed", "unsupported_context"}

    # Derive the resolved effective capacity from any persisted row (all rows share one run's
    # identity). ``prefill_effective_capacity`` takes min(max_context, loaded_context) -- the
    # exact value the suite used to gate prefill eligibility. SQLite REAL affinity coerces the
    # stored token counts to float, so coerce back to int before the isinstance(capacity) guard.
    effective_capacity = None
    for r in rows:
        cap = prefill_effective_capacity(
            {
                "loaded_context": _coerce_capacity(r.get("loaded_context")),
                "max_context": _coerce_capacity(r.get("max_context")),
            }
        )
        if cap is not None:
            effective_capacity = cap
            break

    expected = set()
    for fid in canonical_fixture_ids():
        if effective_capacity is None:
            # Unknown capacity -> conservative: treat the point as skipped, never crashed.
            continue
        try:
            fixture = load_fixture(fid)
        except Exception:  # pragma: no cover - defensive
            expected.add(fid)
            continue
        if prefill_point_eligible(
            effective_capacity,
            actual_input_tokens=fixture.actual_prompt_tokens,
            requested_output_tokens=EVIDENCE_OUTPUT_TOKENS,
        ):
            expected.add(fid)
    # All three generation points are always attempted regardless of capacity.
    expected |= {"8K", "16K", "32K"}

    all_terminal = all(by_fid.get(fid, {}).get("status") in terminal for fid in expected)
    any_completed = any(by_fid.get(fid, {}).get("status") == "completed" for fid in expected)
    return all_terminal and any_completed


def canonical_speed_status(speed_run_id: str) -> tuple[int, dict[str, Any]]:
    """Resolve lifecycle status for a Standard Speed run id.

    Resolution order (canonical authoritative, legacy fallback):

    1. Canonical evidence rows present AND every expected point terminal -> ``200 completed``
       (+ result_url). Partial support is still completion -- unsupported points are terminal.
    2. Canonical rows present but incomplete: live child handle -> ``200 running``; else the
       child crashed -> ``200 failed`` (no premature ``completed`` on partial persistence).
    3. No canonical rows -> fall back to the legacy runner semantics (shared ResultsStore +
       child handle) so historical runs keep working unchanged.
    4. Unsafe / malformed run id -> ``400 invalid run_id``.
    """
    try:
        _validate_speed_run_id(speed_run_id)
    except ValueError:
        return 400, {"error": "Invalid run_id"}

    rows = _canonical_rows_for(speed_run_id)
    if rows:
        if _canonical_suite_complete(rows, speed_run_id):
            return 200, {
                "speed_run_id": speed_run_id,
                "status": "completed",
                "result_url": "/speed/results/" + speed_run_id,
            }
        # Incomplete canonical data: is the child still running or has it crashed?
        active_ids = standard_run_guard.active_ids()  # also prunes exited handles
        if speed_run_id in active_ids:
            return 200, {"speed_run_id": speed_run_id, "status": "running"}
        code = standard_run_guard.exit_code(speed_run_id)
        if code is not None and code != 0:
            return 200, {
                "speed_run_id": speed_run_id,
                "status": "failed",
                "error": f"Speed process exited with status {code}; check the run log for details",
            }
        return 200, {
            "speed_run_id": speed_run_id,
            "status": "failed",
            "error": "Speed process exited without completing all points",
        }

    # --- Legacy fallback (historical runs in the shared ResultsStore) ---
    # Access _get_store dynamically via the module so a monkeypatched store is honoured.
    try:
        import src.v2_speed_suite_runner as _legacy_vs

        legacy_rows = [
            r for r in _legacy_vs._get_store().get_all()
            if (r.get("run_id") or "") == speed_run_id
        ]
    except Exception:  # pragma: no cover - defensive
        legacy_rows = []

    if legacy_rows:
        return 200, {
            "speed_run_id": speed_run_id,
            "status": "completed",
            "result_url": "/speed/results/" + speed_run_id,
        }

    active_ids = standard_run_guard.active_ids()  # also prunes exited handles
    if speed_run_id in active_ids:
        return 200, {"speed_run_id": speed_run_id, "status": "running"}

    code = standard_run_guard.exit_code(speed_run_id)
    if code is not None and code != 0:
        return 200, {
            "speed_run_id": speed_run_id,
            "status": "failed",
            "error": f"Speed process exited with status {code}; check the run log for details",
        }
    if code == 0:
        return 200, {
            "speed_run_id": speed_run_id,
            "status": "failed",
            "error": "Speed process exited without producing a result",
        }
    return 404, {"error": f"Speed run '{speed_run_id}' not found"}
