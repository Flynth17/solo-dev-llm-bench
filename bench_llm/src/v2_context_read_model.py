"""Stable read model for completed Context benchmark runs (RM-26-AA-0009).

Reads an authoritative persisted artifact produced by
:func:`src.v2_context_suite_runner.run_context_suite` and projects it into a
small, intentionally shaped *view model* that the future HTTP API and UI consume.

This is an **adapter / view-model layer only** -- deliberately NOT a second
benchmark engine:

* **Authoritative per-point scores, never recomputed.** Scores, baseline and
  degradation are taken verbatim from the persisted run document. Detailed fact
  evidence exists for *inspection only* -- it is never summed to re-derive an
  authoritative score or degradation value.

* **No benchmark semantics.** It changes nothing about corpus, scoring, context
  points, baseline selection or unsupported handling. Those remain locked in
  :mod:`src.v2_context_suite_runner` / :mod:`src.context_corpus`.

* **Persistence-only dependency.** The only persisted-data reader it uses is
  :mod:`src.v2_context_artifact`. It never imports any executor/builder/scorer.

* **Faithful text.** No whitespace/newline normalisation of stored values; missing
  telemetry stays ``null``, never coerced to zero.
"""

from __future__ import annotations

from typing import Any, Optional

# --- Reuse the persistence-only layer (load + its exceptions). Single source of
# truth for these exception types -- this module re-exports rather than redefining
# them so callers catching either name catch the same class. ---
from src.v2_context_artifact import (
    SCHEMA_VERSION,
    ContextArtifactSchemaError,
    ContextRunNotFoundError,
)
from src.v2_context_artifact import load as _load_document

# Baseline status label surfaced to consumers.
STATUS_SUCCESS = "success"
STATUS_UNSUPPORTED = "unsupported"


# NOTE: ContextRunNotFoundError lives in src.v2_context_artifact (imported above).
class ContextReadModelIntegrityError(ValueError):
    """A persisted artifact failed read-model integrity validation.

    Raised when the authoritative data is internally inconsistent in a way that
    could mislead a consumer (missing points, identity disagreement, baseline
    pointing at a non-supported point). The read model never "repairs" such records
    by recomputing values -- it fails explicitly.
    """


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_integrity(document: dict[str, Any]) -> None:
    """Validate the persisted authoritative document in place.

    Raises :class:`ContextArtifactSchemaError` for a bad/unsupported schema or
    missing structure and :class:`ContextReadModelIntegrityError` for internally
    inconsistent authoritative data. No values are recomputed or repaired.
    """
    if not isinstance(document, dict):
        raise ContextArtifactSchemaError("artifact document must be a mapping")

    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ContextArtifactSchemaError(
            f"unsupported context artifact schema_version {version!r}; this build supports "
            f"{SCHEMA_VERSION}"
        )

    run_id = document.get("context_run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ContextArtifactSchemaError("artifact is missing a valid 'context_run_id'")

    points = document.get("points")
    if not isinstance(points, list) or not points:
        raise ContextArtifactSchemaError("artifact must contain a non-empty 'points' list")

    fingerprint = document.get("configuration_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ContextReadModelIntegrityError(
            "artifact is missing a configuration fingerprint; cannot verify ownership"
        )

    # Every point must carry the fields consumers rely on.
    for point in points:
        if not isinstance(point, dict):
            raise ContextArtifactSchemaError("each point must be a mapping")
        for key in ("context_point", "requested_context_tokens", "status", "score"):
            if key not in point:
                raise ContextArtifactSchemaError(f"point missing required field {key!r}")
        if not _is_int(point["requested_context_tokens"]):
            raise ContextReadModelIntegrityError(
                f"point {point.get('context_point')!r} has invalid requested_context_tokens"
            )

    # Baseline must point at an actual supported point when one is declared.
    baseline = document.get("baseline_context_point")
    if baseline is not None:
        present = {p["context_point"] for p in points}
        if baseline not in present:
            raise ContextReadModelIntegrityError(
                f"declared baseline {baseline!r} does not match any persisted point"
            )


def _project_point(point: dict[str, Any]) -> dict[str, Any]:
    """Project one authoritative point into the view model (verbatim, no recompute)."""
    return {
        "context_point": point.get("context_point"),
        "requested_context_tokens": point.get("requested_context_tokens"),
        "actual_context_tokens": point.get("actual_context_tokens"),
        "status": point.get("status"),
        "score": point.get("score"),
        "facts_requested": point.get("facts_requested"),
        "facts_correct": point.get("facts_correct"),
        "baseline": bool(point.get("baseline")),
        "degradation_from_baseline": point.get("degradation_from_baseline"),
        "retention_relative_to_baseline": point.get("retention_relative_to_baseline"),
        "failure_reason": point.get("failure_reason"),
        # Per-fact evidence is inspection-only; never summed to re-derive score.
        "evidence": point.get("evidence") or [],
        "telemetry": point.get("telemetry") or {},
    }


def build_read_model(document: dict[str, Any]) -> dict[str, Any]:
    """Project a persisted artifact document into the stable read model.

    Validates integrity first (raising on inconsistency) and then surfaces the
    authoritative per-point data verbatim. Does not modify ``document``.
    """
    if not isinstance(document, dict):
        raise ContextArtifactSchemaError("artifact document must be a mapping")

    _validate_integrity(document)

    points = sorted(document.get("points", []), key=lambda p: p["requested_context_tokens"])
    supported = [p for p in points if p.get("status") == STATUS_SUCCESS]
    gaps = [p for p in points if p.get("status") != STATUS_SUCCESS]

    return {
        "run_id": document.get("context_run_id"),
        "artifact_type": document.get("artifact_type"),
        "classification": document.get("classification"),
        "model_key": document.get("model_key"),
        "model_display_name": document.get("model_display_name"),
        "model_quantization": document.get("model_quantization"),
        "configuration_fingerprint": document.get("configuration_fingerprint"),
        "lm_studio_url": document.get("lm_studio_url"),
        "effective_capacity": document.get("effective_capacity"),
        "baseline_context_point": document.get("baseline_context_point"),
        "benchmark_schema_version": document.get("benchmark_schema_version"),
        "context_points_contract": document.get("context_points_contract"),
        "supported_point_count": len(supported),
        "gap_point_count": len(gaps),
        "points": [_project_point(p) for p in points],
    }


def load_context_result(run_id: str) -> dict[str, Any]:
    """Load a persisted Context run and return its read model.

    Propagates :class:`ContextRunNotFoundError` (unknown run) and
    :class:`ContextArtifactSchemaError` (unsupported/malformed schema) from the
    persistence layer; :class:`ContextReadModelIntegrityError` is raised here if the
    loaded data fails integrity validation.
    """
    document = _load_document(run_id)
    return build_read_model(document)
