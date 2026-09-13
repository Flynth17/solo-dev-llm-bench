"""Durable persistence for completed V2 quality suite-runner runs (Act 2).

Stores the authoritative, post-aggregation run document produced by
:func:`src.v2_quality_suite_runner.run_v2_run` so a completed run can be loaded
later by ``run_id`` for the read-only UI.

Design constraints honoured by this module:

* **Persistence only.** This module never imports or invokes any benchmark
  executor, validator, corpus, LM Studio transport, subprocess or legacy code
  path. It serialises already-computed :class:`SuiteResult` documents plus the
  coordinator's aggregate. It cannot change benchmark execution semantics.

* **No call to ``run_v2_quality_live`` / no legacy executor import.** This module
  never imports :mod:`src.v2_quality_executor`, so it can neither import nor call
  its superseded ``run_v2_quality_live`` entry point. The only benchmark symbol it
  reads are the *immutable canonical constants* from the suite runner, purely for
  light integrity validation of what was already produced.

* **Authoritative, not recomputed.** Canonical per-suite scores and the ``/166``
  total are stored verbatim from the coordinator's aggregate -- a consumer must
  never re-derive them by summing validator result rows later.

* **Faithful text preservation.** No ``.strip`` / ``.rstrip`` / whitespace or
  newline normalisation is applied to any stored value; every stored string
  round-trips byte-for-byte through the artifact. Missing telemetry stays missing
  (``null``), never converted into zero.

* **Atomic write.** Written to a temp file, flushed + fsync'd, then atomically
  ``os.replace``-d into place so a partial/crashed write never masquerades as a
  valid finished run and never overwrites an existing artifact spuriously.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

# Read-only immutable canonical constants from the locked suite runner, used only
# to sanity-check what was already produced. This never imports the legacy
# executor and never calls any execution path.
from src.v2_quality_suite_runner import (  # noqa: E402
    CANONICAL_DENOMINATORS,
    TOTAL_DENOMINATOR,
    SUITES,
)

# ---------------------------------------------------------------------------
# Artifact identity / location
# ---------------------------------------------------------------------------

# Schema version of *this persistence format* -- unrelated to "V2" benchmark.
# Bump only when the on-disk document shape evolves; old artifacts keep their own
# version and are rejected (or migrated later) rather than misread as new.
SCHEMA_VERSION: int = 1

ARTIFACT_TYPE: str = "solo-dev-llm-bench.v2-run"

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
V2_RUNS_DIR = _DEFAULT_DATA_DIR / "v2_runs"


class V2RunNotFoundError(KeyError):
    """Raised when no persisted V2 run document exists for the given ``run_id``."""


class V2ArtifactSchemaError(ValueError):
    """Raised when a stored artifact's schema version or shape is unsupported."""


# ---------------------------------------------------------------------------
# run_id -> path (with traversal guards; the id becomes a filename)
# ---------------------------------------------------------------------------

def _validate_run_id(run_id: Any) -> str:
    """Return a safe string ``run_id`` or raise ``ValueError``.

    A ``run_id`` is used to build a filesystem path, so reject anything that could
    escape the artifact directory (absolute paths, separators, null bytes).
    """
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty string")
    if "\x00" in run_id or "/" in run_id or "\\" in run_id or os.sep in run_id:
        raise ValueError(f"invalid run_id (path traversal guard): {run_id!r}")
    return run_id


def artifact_path(run_id: str) -> Path:
    """Return the deterministic artifact path for ``run_id``."""
    return V2_RUNS_DIR / f"{_validate_run_id(run_id)}.json"


# ---------------------------------------------------------------------------
# Document construction (authoritative, verbatim)
# ---------------------------------------------------------------------------

def build_run_document(aggregate: dict[str, Any], suite_results: List[Any]) -> dict[str, Any]:
    """Assemble the versioned run document from a completed run.

    ``aggregate`` is the coordinator's authoritative output from
    :func:`src.v2_quality_suite_runner.combine_suite_results`; ``suite_results``
    are the five :class:`SuiteResult` documents (anything exposing
    ``to_dict()``). Nothing is recomputed: the aggregate and each suite document
    are stored verbatim.

    Raises ``ValueError`` if inputs have an unexpected type so misuse fails loudly
    at build time rather than silently storing a corrupt artifact.
    """
    if not isinstance(aggregate, dict):
        raise TypeError("aggregate must be the coordinator aggregate dict")
    if not isinstance(suite_results, list) or not suite_results:
        raise TypeError("suite_results must be a non-empty list of SuiteResult documents")

    # Light structural sanity -- never re-derives scores, just guards shape. The
    # canonical-denominator contract itself is enforced upstream by the coordinator.
    checks_total = aggregate.get("checks_total")
    if not isinstance(checks_total, int) or isinstance(checks_total, bool):
        raise ValueError("aggregate is missing a valid integer 'checks_total'")

    suites_out: List[dict[str, Any]] = []
    for item in suite_results:
        to_dict = getattr(item, "to_dict", None)
        if callable(to_dict):
            suites_out.append(dict(to_dict()))
        elif isinstance(item, dict):
            suites_out.append(dict(item))
        else:
            raise TypeError(f"each suite result must expose to_dict() or be a dict, got {item!r}")

    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": aggregate.get("run_id"),
        "model_identifier": aggregate.get("model_identifier"),
        "configuration_fingerprint": aggregate.get("configuration_fingerprint"),
        "classification": aggregate.get("classification"),
        "reasoning_policy": aggregate.get("reasoning_policy"),
        # Authoritative coordinator aggregate, stored verbatim (never re-summed).
        "aggregate": dict(aggregate),
        # Full authoritative per-suite documents (identity + config + failures + telemetry).
        "suites": suites_out,
    }


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, obj: Any) -> None:
    """Write ``obj`` as UTF-8 JSON to ``path`` atomically.

    Temp file -> flush + fsync -> ``os.replace`` into place. On any failure the
    temp file is removed so no half-written artifact is ever left behind, and the
    existing target (if any) is never partially overwritten.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # atomic on POSIX and Windows
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def persist_run(aggregate: dict[str, Any], suite_results: List[Any]) -> Path:
    """Persist a completed run document atomically; return its path.

    The ``run_id`` is taken from the aggregate so a persisted artifact can always be
    loaded back via :func:`load(run_id)`. Raises on invalid input or write failure.
    """
    document = build_run_document(aggregate, suite_results)
    _atomic_write_json(artifact_path(document["run_id"]), document)
    return artifact_path(document["run_id"])


def persist_document(document: dict[str, Any]) -> Path:
    """Persist a pre-built document atomically; return its path."""
    if not isinstance(document, dict):
        raise TypeError("document must be a dict")
    run_id = document.get("run_id")
    _atomic_write_json(artifact_path(run_id), document)
    return artifact_path(run_id)


# ---------------------------------------------------------------------------
# Read-only loader
# ---------------------------------------------------------------------------

def load(run_id: str) -> dict[str, Any]:
    """Load a persisted V2 run document by ``run_id``.

    Returns the full artifact dict. Raises :class:`V2RunNotFoundError` when no such
    run exists and :class:`V2ArtifactSchemaError` when the stored schema version is
    unsupported or the shape is malformed.
    """
    path = artifact_path(run_id)
    if not path.exists():
        raise V2RunNotFoundError(run_id)

    with open(path, "r", encoding="utf-8") as fh:
        document = json.load(fh)

    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise V2ArtifactSchemaError(
            f"unsupported v2 artifact schema_version {version!r}; this build supports "
            f"{SCHEMA_VERSION}"
        )
    if not isinstance(document.get("aggregate"), dict):
        raise V2ArtifactSchemaError("v2 artifact is missing an 'aggregate' object")
    if document.get("run_id") != run_id:
        raise V2ArtifactSchemaError(
            f"stored run_id {document.get('run_id')!r} does not match requested {run_id!r}"
        )

    return document


def try_load(run_id: str) -> Optional[dict[str, Any]]:
    """Return the artifact dict or ``None`` when the run is not found.

    Convenience for read-only consumers that prefer a sentinel over an exception.
    Still raises :class:`V2ArtifactSchemaError` on a present-but-malformed artifact.
    """
    try:
        return load(run_id)
    except V2RunNotFoundError:
        return None
