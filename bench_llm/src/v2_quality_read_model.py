"""Stable read model for completed V2 quality runs (Act 3).

Reads an authoritative persisted artifact produced by
:func:`src.v2_quality_artifact.build_run_document` and projects it into a small,
intentionally shaped *view model* that the future HTTP API and UI consume.

This module is an **adapter / view-model layer only**. It is deliberately NOT a
second benchmark engine:

* **Authoritative aggregate, never recomputed.** Canonical scores (the ``/166``
  total and every per-suite ``passed``/``total``) are taken verbatim from the
  persisted coordinator aggregate. Detailed rows (validation/case/diagnostic
  ``checks_total``, failure rows, request records) exist for *inspection only* --
  they are never summed to derive canonical scores.

* **No benchmark semantics.** It changes nothing about corpus, validators,
  scoring, denominators, reasoning policy, output-budget policy or Markdown byte
  handling. Those remain locked in :mod:`src.v2_quality_suite_runner`.

* **Legacy executor isolation.** This module never imports or calls
  ``run_v2_quality_live`` and has no dependency on :mod:`src.v2_quality_executor`.
  The only persisted-data reader it uses is the persistence-only
  :mod:`src.v2_quality_artifact` layer. Canonical identity data (denominators /
  suite set) are declared here as immutable constants mirroring the locked
  runner contract -- they are static identity values, not scoring logic.

* **Faithful text.** No ``.strip`` / ``.rstrip`` / whitespace or newline
  normalisation is applied to any persisted string; evidence round-trips exactly.
  Missing telemetry stays ``null``, never coerced to zero.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# --- Reuse the persistence-only Act 2 layer (load + its exceptions). ---
from src.v2_quality_artifact import (
    SCHEMA_VERSION,
    V2ArtifactSchemaError,
)
from src.v2_quality_artifact import (
    load as _load_artifact,
)

# ---------------------------------------------------------------------------
# Locked canonical identity data.
#
# These mirror the immutable contract in src/v2_quality_suite_runner exactly. They
# are static identity/scope constants (the suite set and its fixed denominators),
# not scoring or aggregation logic, so declaring them here keeps this read model
# free of any dependency on the (superseded) legacy executor import graph while
# still validating against the same authoritative values. Bump only if and when
# the locked benchmark contract itself changes.
# ---------------------------------------------------------------------------

CANONICAL_DENOMINATORS: dict[str, int] = {
    "python": 58,
    "java": 52,
    "markdown": 20,
    "evidence": 30,
    "drift": 6,
}
SUITES: tuple[str, ...] = ("python", "java", "markdown", "evidence", "drift")
TOTAL_DENOMINATOR: int = sum(CANONICAL_DENOMINATORS.values())  # == 166

# Configuration fields that must agree across all five suite documents. These are
# benchmark-configuration/identity values, so genuine disagreement indicates a
# corrupt or mixed artifact and must fail explicitly (never silently chosen).
CONFIG_IDENTITY_FIELDS: tuple[str, ...] = (
    "run_id",
    "model_identifier",
    "configuration_fingerprint",
    "reasoning_policy",
    "effective_context_capacity",
    "output_budget_policy",
    "output_budget_percent",
    "requested_max_output_tokens",
    "model_max_context",
    "loaded_context",
    "temperature",
    "loaded_reasoning_mode",
    "mtp_state",
    "speculative_simple",
)


class V2ReadModelIntegrityError(ValueError):
    """A persisted artifact failed read-model integrity validation.

    Raised when the authoritative data is internally inconsistent in a way that
    could mislead a consumer (wrong suite set, non-canonical denominators,
    identity disagreement between aggregate and suites, or aggregate-vs-suite
    score mismatch). The read model never "repairs" such records by recomputing
    canonical values -- it fails explicitly.
    """


# ---------------------------------------------------------------------------
# Presentation math (deterministic)
# ---------------------------------------------------------------------------

def round_percentage(passed: int, total: int) -> float:
    """Return ``passed / total * 100`` rounded to one decimal place.

    Uses ``ROUND_HALF_UP`` for a deterministic, display-friendly rule so identical
    inputs always produce identical output (e.g. ``148/166 -> 89.2``). This is
    presentation math over the authoritative aggregate -- it never recomputes the
    aggregate itself.
    """
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        return 0.0
    pct = (Decimal(passed) / Decimal(total) * Decimal(100)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return float(pct)


# ---------------------------------------------------------------------------
# Integrity validation (authoritative data only; never repairs anything)
# ---------------------------------------------------------------------------

def _validate_integrity(document: dict[str, Any]) -> None:
    """Validate the persisted authoritative document in place.

    Raises :class:`V2ArtifactSchemaError` for a bad/unsupported schema or missing
    structure and :class:`V2ReadModelIntegrityError` for internally inconsistent
    authoritative data. No values are recomputed or repaired -- inconsistencies
    simply fail loudly.
    """
    if not isinstance(document, dict):
        raise V2ArtifactSchemaError("artifact document must be a mapping")

    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise V2ArtifactSchemaError(
            f"unsupported v2 artifact schema_version {version!r}; this build supports "
            f"{SCHEMA_VERSION}"
        )

    aggregate = document.get("aggregate")
    suites = document.get("suites")
    if not isinstance(aggregate, dict):
        raise V2ArtifactSchemaError("artifact is missing an 'aggregate' object")
    if not isinstance(suites, list) or len(suites) != len(SUITES):
        raise V2ArtifactSchemaError(
            f"artifact must contain exactly {len(SUITES)} suite documents"
        )

    # Index suites by name; reject unknown/duplicate suite names early.
    docs: dict[str, dict[str, Any]] = {}
    for doc in suites:
        if not isinstance(doc, dict):
            raise V2ArtifactSchemaError("each suite document must be a mapping")
        name = doc.get("suite")
        if name not in CANONICAL_DENOMINATORS:
            raise V2ArtifactSchemaError(f"unexpected suite {name!r} in artifact")
        if name in docs:
            raise V2ArtifactSchemaError(f"duplicate suite {name!r} in artifact")
        docs[name] = doc

    # Exactly the five canonical suites, no more, no less.
    if set(docs) != set(CANONICAL_DENOMINATORS):
        raise V2ReadModelIntegrityError(
            f"suite set mismatch: present {sorted(docs)}; expected {sorted(CANONICAL_DENOMINATORS)}"
        )

    # Canonical total and per-suite denominators.
    if not isinstance(aggregate.get("checks_total"), int) or isinstance(
        aggregate.get("checks_total"), bool
    ):
        raise V2ReadModelIntegrityError("aggregate 'checks_total' is missing/invalid")
    if aggregate["checks_total"] != TOTAL_DENOMINATOR:
        raise V2ReadModelIntegrityError(
            f"aggregate checks_total {aggregate['checks_total']} != canonical {TOTAL_DENOMINATOR}"
        )

    per_suite = aggregate.get("per_suite")
    if not isinstance(per_suite, dict):
        raise V2ReadModelIntegrityError("aggregate 'per_suite' is missing/invalid")

    # Per-suite denominators must equal the canonical constants AND must match each
    # SuiteResult's own score fields. Never recompute -- compare authoritative to
    # authoritative and fail on disagreement.
    for suite in SUITES:
        denom = CANONICAL_DENOMINATORS[suite]
        entry = per_suite.get(suite)
        if not isinstance(entry, dict) or "passed" not in entry or "total" not in entry:
            raise V2ReadModelIntegrityError(
                f"aggregate per_suite[{suite!r}] is missing passed/total"
            )
        if entry["total"] != denom:
            raise V2ReadModelIntegrityError(
                f"aggregate per_suite[{suite!r}].total {entry['total']} != canonical {denom}"
            )
        doc = docs[suite]
        doc_passed = doc.get("checks_passed")
        doc_total = doc.get("checks_total")
        if not _is_int(doc_passed) or not _is_int(doc_total):
            raise V2ReadModelIntegrityError(
                f"suite {suite!r} has non-integer checks fields"
            )
        if (doc_passed, doc_total) != (entry["passed"], entry["total"]):
            raise V2ReadModelIntegrityError(
                f"aggregate score for {suite!r} "
                f"{entry['passed']}/{entry['total']} != persisted SuiteResult "
                f"{doc_passed}/{doc_total}"
            )

    # Identity consistency: aggregate + every suite must agree on identity/config.
    _validate_identity_consistency(aggregate, docs)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_identity_consistency(aggregate: dict[str, Any], docs: dict[str, dict]) -> None:
    """Ensure aggregate and all suites agree on shared identity/config fields."""
    # Cross-check the scalar identity fields between aggregate and each suite.
    for field in ("run_id", "model_identifier", "configuration_fingerprint", "reasoning_policy"):
        agg_value = aggregate.get(field)
        for suite, doc in docs.items():
            if doc.get(field) != agg_value:
                raise V2ReadModelIntegrityError(
                    f"identity field {field!r} differs between aggregate and suite {suite!r}"
                )

    # Configuration fields must be identical across every suite document.
    for field in CONFIG_IDENTITY_FIELDS:
        values = {doc.get(field) for doc in docs.values()}
        if len(values) > 1:
            raise V2ReadModelIntegrityError(
                f"configuration field {field!r} differs across suites: "
                f"{sorted(repr(v) for v in values)}"
            )


# ---------------------------------------------------------------------------
# Normalisation helpers (no normalisation of string content)
# ---------------------------------------------------------------------------

def _locator(suite: str, source_type: str, identifier: Any) -> str:
    """Build a stable inspection locator from available identity."""
    return f"{suite}/{source_type}/{identifier}"


def _telemetry_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    """Extract per-request telemetry, preserving null (never zeroing)."""
    return {
        "ttft_seconds": record.get("ttft_seconds"),
        "prefill_throughput": record.get("prefill_throughput"),
        "decode_throughput": record.get("decode_throughput"),
        "reasoning_tokens": record.get("reasoning_tokens"),
    }


# ---------------------------------------------------------------------------
# Failure / success normalisation (failure-first)
# ---------------------------------------------------------------------------

def _normalize_failures(docs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a deterministic failure-inspection list.

    Order is canonical suite order; within a suite, extraction failures precede
    validation failures, each preserving its original source order. Every record
    carries the fields the UI needs for first-class inspection while keeping the
    original evidence verbatim (no string normalisation).
    """
    failures: list[dict[str, Any]] = []

    for suite in SUITES:
        doc = docs[suite]
        checks_passed = doc.get("checks_passed")
        checks_total = doc.get("checks_total")

        # Extraction failures (whole-request deliverable not produced/extracted).
        for item in doc.get("extraction_failures") or []:
            request_id = item.get("request_id")
            failures.append({
                "source_type": "extraction",
                "suite": suite,
                "case_id": None,
                "request_id": request_id if request_id else None,
                "failure_type": "extraction",
                "failure_reason": item.get("reason"),
                "checks_passed": checks_passed,
                "checks_total": checks_total,
                "expected": None,
                "actual": None,
                "extraction_classification": "failure",
                "validation_classification": "unavailable",
                "locator": _locator(suite, "extraction", request_id),
            })

        # Validation failures (individual check-level failures). case_id is the
        # stable per-case identifier; we never invent a request linkage.
        for item in doc.get("validation_failures") or []:
            case_id = item.get("case_id")
            name = item.get("name") or ""
            failures.append({
                "source_type": "validation",
                "suite": suite,
                "case_id": case_id if case_id else None,
                "request_id": None,
                "failure_type": item.get("failure_type"),
                "failure_reason": item.get("failure_reason"),
                "checks_passed": checks_passed,
                "checks_total": checks_total,
                "expected": None,
                "actual": None,
                "extraction_classification": "unavailable",
                "validation_classification": "failure",
                "locator": _locator(suite, "validation", case_id if case_id else name),
            })

    return failures


def _normalize_successes(docs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Return request-level success records for successfully-extracted requests.

    Ordered by canonical suite then original request order. Each record keeps the
    validator-level pass/total (inspectional) plus a telemetry snapshot; missing
    telemetry stays null. Requests whose extraction failed are excluded here --
    they appear only in :func:`_normalize_failures`.
    """
    successes: list[dict[str, Any]] = []

    for suite in SUITES:
        doc = docs[suite]
        for record in doc.get("requests") or []:
            if not record.get("extraction_success"):
                continue
            request_id = record.get("request_id")
            successes.append({
                "suite": suite,
                "request_id": request_id if request_id else None,
                "locator": _locator(suite, "request", request_id),
                "source_type": "request",
                "extraction_classification": "success",
                "checks_passed": record.get("checks_passed"),
                "checks_total": record.get("checks_total_validator"),
                "telemetry": _telemetry_snapshot(record),
            })

    return successes


def _normalize_telemetry(docs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Return per-suite telemetry (canonical order). Missing stays null."""
    out = []
    for suite in SUITES:
        doc = docs[suite]
        out.append({
            "suite": suite,
            "wall_time_seconds": doc.get("wall_time_seconds"),
            "ttft_seconds": doc.get("ttft_seconds"),
            "prefill_throughput": doc.get("prefill_throughput"),
            "decode_throughput": doc.get("decode_throughput"),
            "prompt_tokens": doc.get("prompt_tokens"),
            "completion_tokens": doc.get("completion_tokens"),
            "reasoning_tokens": doc.get("reasoning_tokens"),
        })
    return out


def _resolve_configuration(docs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Resolve the shared configuration block from consistent suite documents."""
    config: dict[str, Any] = {}
    for field in CONFIG_IDENTITY_FIELDS:
        values = {doc.get(field) for doc in docs.values()}
        # Consistency was already enforced by _validate_identity_consistency; take
        # the single value (null only when every suite is null/absent).
        config[field] = next(iter(values)) if len(values) == 1 else None
    return config


# ---------------------------------------------------------------------------
# Primary read-model entry points
# ---------------------------------------------------------------------------

def build_read_model(document: dict[str, Any]) -> dict[str, Any]:
    """Project a persisted artifact document into the stable read model.

    Validates integrity first (raising on inconsistency) and then views the
    authoritative aggregate as canonical while projecting detailed rows for
    inspection. Does not modify ``document``.
    """
    if not isinstance(document, dict):
        raise V2ArtifactSchemaError("artifact document must be a mapping")

    _validate_integrity(document)

    aggregate = document["aggregate"]
    docs: dict[str, dict[str, Any]] = {doc["suite"]: doc for doc in document["suites"]}

    checks_passed = aggregate.get("checks_passed")
    checks_total = aggregate.get("checks_total")

    suites_out = [
        {
            "suite": suite,
            # Authoritative per-suite scores from the persisted aggregate.
            "checks_passed": aggregate["per_suite"][suite]["passed"],
            "checks_total": aggregate["per_suite"][suite]["total"],
            "failed_checks": (
                aggregate["per_suite"][suite]["total"]
                - aggregate["per_suite"][suite]["passed"]
            ),
        }
        for suite in SUITES
    ]

    run_out = {
        "run_id": document.get("run_id"),
        "model_identifier": aggregate.get("model_identifier"),
        "configuration_fingerprint": aggregate.get("configuration_fingerprint"),
        "classification": aggregate.get("classification"),
        "reasoning_policy": aggregate.get("reasoning_policy"),
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        # Presentation percentage over the authoritative aggregate.
        "percentage": round_percentage(checks_passed, checks_total) if _is_int(checks_passed) else None,
    }

    return {
        "run": run_out,
        "suites": suites_out,
        "configuration": _resolve_configuration(docs),
        "failures": _normalize_failures(docs),
        "successes": _normalize_successes(docs),
        "telemetry": {"suites": _normalize_telemetry(docs)},
    }


def load_v2_result(run_id: str) -> dict[str, Any]:
    """Load a persisted V2 run and return its read model.

    Propagates :class:`V2RunNotFoundError` (unknown run) and
    :class:`V2ArtifactSchemaError` (unsupported/malformed schema) from the
    persistence layer; :class:`V2ReadModelIntegrityError` is raised here if the
    loaded data fails integrity validation.
    """
    document = _load_artifact(run_id)
    return build_read_model(document)
