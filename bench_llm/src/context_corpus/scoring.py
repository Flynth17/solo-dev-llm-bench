"""Deterministic scoring for the Context benchmark family.

Scoring is exact-match against the *known* fact values produced by
:mod:`src.context_corpus.builder`. There is no judge and no fuzzy matching beyond
a single, conservative whitespace normalization on the model's returned value --
the expected values contain no leading/trailing whitespace, so a trailing newline
from the model must be stripped before comparison (otherwise a correct answer is
falsely failed).

No composite / overall score is ever computed here. Each context point scores
itself as ``correct_facts / requested_facts``; degradation is a plain delta from
the baseline point, computed only by the read-model layer over already-scored
points -- never by summing raw evidence to re-derive an authoritative value.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence, Tuple

# --- Point-level status taxonomy (never flattened). -------------------------
STATUS_SUCCESS = "success"                 # scored; every requested fact was gradeable
STATUS_EXTRACTION_FAILURE = "extraction_failure"  # no usable answer text from the model
STATUS_MALFORMED = "malformed"             # answer present but not parseable into KEY: value
STATUS_FAILED = "failed"                   # operational failure (HTTP/timeout/persistence)
STATUS_UNSUPPORTED = "unsupported"         # requested point above effective capacity

# Operational failures that must NOT be read as capability degradation.
OPERATIONAL_FAILURE_STATUSES = frozenset(
    {STATUS_FAILED, STATUS_EXTRACTION_FAILURE, STATUS_MALFORMED}
)


def _normalize(value: Optional[str]) -> Optional[str]:
    """Strip surrounding whitespace from a returned value for comparison.

    Returns ``None`` when there is nothing to compare (no answer / empty). Never
    alters the *expected* value; only normalizes the model's raw response so a
    trailing newline does not turn a correct answer into a false failure.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def point_score(
    correct: int,
    requested: int,
) -> Optional[float]:
    """Return ``correct / requested`` in ``[0.0, 1.0]``, or ``None`` when unscored.

    ``requested == 0`` yields ``None`` (no gradeable evidence) -- never ``0.0`` --
    so a point with no gradeable facts is an honest "unavailable", distinct from a
    point that scored zero correctness.
    """
    if not isinstance(requested, int) or isinstance(requested, bool):
        raise ValueError("requested must be an integer")
    if requested <= 0:
        return None
    if correct < 0:
        raise ValueError("correct must be >= 0")
    if correct > requested:
        raise ValueError("correct cannot exceed requested")
    return round(correct / requested, 6)


def degradation_from_baseline(
    score: Optional[float],
    baseline_score: Optional[float],
) -> Optional[float]:
    """Return ``score - baseline_score`` (a drift), or ``None`` if either is None.

    Negative means correctness dropped below the baseline; positive means it
    improved. Never fabricates a value from missing inputs.
    """
    if score is None or baseline_score is None:
        return None
    return round(score - baseline_score, 6)


def retention_relative_to_baseline(
    score: Optional[float],
    baseline_score: Optional[float],
) -> Optional[float]:
    """Return ``score / baseline_score`` (retention ratio), or ``None`` when undefined.

    Returns ``None`` when the baseline is missing OR zero -- dividing by a zero
    baseline would fabricate a meaningless infinity/ratio, so we report unavailable
    instead. A model that scored exactly zero at baseline is therefore not turned
    into a spurious 1.0 retention for everyone.
    """
    if score is None or baseline_score is None:
        return None
    if baseline_score == 0:
        return None
    return round(score / baseline_score, 6)


def evaluate_fact(
    key: str,
    expected: str,
    actual_raw: Optional[str],
) -> Tuple[bool, Optional[str]]:
    """Score one fact by exact match.

    Returns ``(passed, normalized_actual)``. ``normalized_actual`` is ``None`` when
    the model produced no comparable value (recorded distinctly from an empty
    string the model actually returned).
    """
    actual = _normalize(actual_raw)
    if actual is None:
        return False, None
    return actual == expected.strip(), actual


def summarize_point(
    records: Sequence[Tuple[str, str, Optional[str], bool]],
    status: str = STATUS_SUCCESS,
) -> dict:
    """Aggregate per-fact ``(key, expected, actual, passed)`` records into a point summary.

    ``score`` is ``None`` when nothing was gradeable (never coerced to zero). The
    returned dict carries everything the read model / UI needs for one point without
    re-deriving anything: status, counts, score, per-fact evidence and failure detail.
    """
    total = len(records)
    correct = sum(1 for *_rest, passed in records if passed)
    failed_keys = [key for key, *_rest, passed in records if not passed]
    return {
        "status": status,
        "facts_requested": total,
        "facts_correct": correct,
        "score": point_score(correct, total),
        "failed_fact_keys": failed_keys,
        "evidence": [
            {"key": key, "expected": exp, "actual": act, "passed": passed}
            for key, exp, act, passed in records
        ],
    }
