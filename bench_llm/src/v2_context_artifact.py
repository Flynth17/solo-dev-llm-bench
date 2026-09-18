"""Durable persistence for completed Context benchmark runs (RM-26-AA-0009).

Stores the authoritative, post-run run document produced by
:func:`src.v2_context_suite_runner.run_context_suite` so a completed run can be
loaded later by ``run_id`` for the read-only UI / API.

Design constraints honoured by this module (identical in spirit to
:mod:`src.v2_quality_artifact`):

* **Persistence only.** Never imports or invokes any benchmark executor, builder,
  scorer, corpus, LM Studio transport or legacy code path. It serialises
  already-computed run documents plus per-point evidence. It cannot change
  benchmark execution semantics.

* **Authoritative, not recomputed.** Per-point scores and the baseline are stored
  verbatim from what the runner produced; a consumer must never re-derive them by
  summing fact rows later.

* **Faithful text preservation.** No ``.strip`` / ``.rstrip`` / whitespace or
  newline normalisation is applied to any stored value; every stored string
  round-trips byte-for-byte. Missing telemetry stays missing (``null``), never
  converted into zero.

* **Atomic write.** Temp file -> flush + fsync -> ``os.replace`` into place so a
  partial/crashed write never masquerades as a valid finished run and never
  overwrites an existing artifact spuriously.

* **Independent run identity.** Each run is one file named by its ``run_id``;
  repeated executions with different ids never overwrite prior evidence.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION: int = 1
ARTIFACT_TYPE: str = "solo-dev-llm-bench.context-run"

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONTEXT_RUNS_DIR = _DEFAULT_DATA_DIR / "context_runs"


class ContextRunNotFoundError(KeyError):
    """Raised when no persisted Context run document exists for the given ``run_id``."""


class ContextArtifactSchemaError(ValueError):
    """Raised when a stored artifact's schema version or shape is unsupported."""


def _validate_run_id(run_id: Any) -> str:
    """Return a safe string ``run_id`` or raise ``ValueError`` (path-traversal guard)."""
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty string")
    if "\x00" in run_id or "/" in run_id or "\\" in run_id or os.sep in run_id:
        raise ValueError(f"invalid run_id (path traversal guard): {run_id!r}")
    return run_id


def artifact_path(run_id: str) -> Path:
    """Return the deterministic artifact path for ``run_id``."""
    return CONTEXT_RUNS_DIR / f"{_validate_run_id(run_id)}.json"


def _atomic_write_json(path: Path, obj: Any) -> None:
    """Write ``obj`` as UTF-8 JSON to ``path`` atomically."""
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


def build_run_document(run: dict[str, Any]) -> dict[str, Any]:
    """Assemble the versioned run document from a completed run.

    ``run`` is the runner's authoritative summary document (identity + per-point
    evidence). Nothing is recomputed -- it is stored verbatim under a schema wrapper.
    Raises ``ValueError`` on unexpected input so misuse fails loudly at build time.
    """
    if not isinstance(run, dict):
        raise TypeError("run must be the runner summary dict")
    if not isinstance(run.get("context_run_id"), str) or not run["context_run_id"]:
        raise ValueError("run document is missing a valid 'context_run_id'")
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **run,
    }


def persist_run(run: dict[str, Any]) -> Path:
    """Persist a completed run document atomically; return its path."""
    document = build_run_document(run)
    _atomic_write_json(artifact_path(document["context_run_id"]), document)
    return artifact_path(document["context_run_id"])


def persist_document(document: dict[str, Any]) -> Path:
    """Persist a pre-built document atomically; return its path."""
    if not isinstance(document, dict):
        raise TypeError("document must be a dict")
    run_id = document.get("context_run_id")
    _atomic_write_json(artifact_path(run_id), document)
    return artifact_path(run_id)


def load(run_id: str) -> dict[str, Any]:
    """Load a persisted Context run document by ``run_id``.

    Raises :class:`ContextRunNotFoundError` when no such run exists and
    :class:`ContextArtifactSchemaError` when the stored schema version or shape is
    unsupported.
    """
    path = artifact_path(run_id)
    if not path.exists():
        raise ContextRunNotFoundError(run_id)

    with open(path, "r", encoding="utf-8") as fh:
        document = json.load(fh)

    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ContextArtifactSchemaError(
            f"unsupported context artifact schema_version {version!r}; this build supports "
            f"{SCHEMA_VERSION}"
        )
    if not isinstance(document.get("context_run_id"), str):
        raise ContextArtifactSchemaError("context artifact is missing a 'context_run_id'")
    if document.get("context_run_id") != run_id:
        raise ContextArtifactSchemaError(
            f"stored context_run_id {document.get('context_run_id')!r} does not match requested {run_id!r}"
        )
    return document


def try_load(run_id: str) -> Optional[dict[str, Any]]:
    """Return the artifact dict or ``None`` when the run is not found."""
    try:
        return load(run_id)
    except ContextRunNotFoundError:
        return None
