"""Deterministic validator for the unsolvable (contradictory-requirements) task.

Accepts only structured responses that correctly identify the specification as
impossible.  Scoring is fully deterministic — no LLM-as-judge.

Expected response format::

    IMPOSSIBLE: yes
    CLASS: contradictory-requirements
    CONFLICT: R1, R2
    EXPLANATION: <at least 50 characters of reasoning>

Correct result: score=1.0, passed=True
Anything else: score=0.0, passed=False
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Set


# ------------------------------------------------------------------
# Result dataclass
# ------------------------------------------------------------------

@dataclass
class UnsolvableResult:
    """Result of the unsolvable-task validation."""

    score: float
    passed: bool
    impossible_detected: bool
    classification: str
    conflict_ids: Set[str]
    explanation_valid: bool
    error: str = ""


# ------------------------------------------------------------------
# Validation logic
# ------------------------------------------------------------------

_REQUIRED_CONFLICT_IDS: Set[str] = {"R1", "R2"}

_VALID_CLASSES: Set[str] = frozenset({
    "contradictory-requirements",
    "contradictory",
    "self-contradictory",
    "logically-contradictory",
})


def _extract_field(text: str, field_name: str) -> str:
    """Extract the value after ``<field_name>: `` (case-insensitive marker).

    Returns the raw value stripped of whitespace.
    """
    pattern = re.compile(
        r"(?i)^" + re.escape(field_name) + r"\s*:\s*(.*)$", re.MULTILINE
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def validate_unsolvable_response(response: str) -> UnsolvableResult:
    """Validate a model response to the unsolvable task.

    Args:
        response: The raw model output string.

    Returns:
        UnsolvableResult with deterministic scoring.
    """
    # --- Empty / whitespace response ---
    if not response or not response.strip():
        return UnsolvableResult(
            score=0.0,
            passed=False,
            impossible_detected=False,
            classification="",
            conflict_ids=set(),
            explanation_valid=False,
            error="empty response",
        )

    impossible_raw = _extract_field(response, "IMPOSSIBLE")
    class_raw = _extract_field(response, "CLASS")
    conflict_raw = _extract_field(response, "CONFLICT")
    explanation_raw = _extract_field(response, "EXPLANATION")

    # --- IMPOSSIBLE must be "yes" (case-insensitive) ---
    impossible_detected = impossible_raw.lower() == "yes"

    if not impossible_detected:
        return UnsolvableResult(
            score=0.0,
            passed=False,
            impossible_detected=False,
            classification=class_raw,
            conflict_ids=set(),
            explanation_valid=False,
            error="IMPOSSIBLE marker is not 'yes'",
        )

    # --- CLASS must be one of the valid contradictory classifications ---
    classification = class_raw.strip().lower()
    if classification not in _VALID_CLASSES:
        return UnsolvableResult(
            score=0.0,
            passed=False,
            impossible_detected=True,
            classification=classification,
            conflict_ids=set(),
            explanation_valid=False,
            error="invalid CLASS",
        )

    # --- CONFLICT must identify BOTH R1 and R2 ---
    conflict_ids: Set[str] = set()
    if conflict_raw:
        # Match R1, R2, etc. (case-insensitive)
        found = re.findall(r"R[1-9]", conflict_raw, re.IGNORECASE)
        conflict_ids = {r.upper() for r in found}

    if not _REQUIRED_CONFLICT_IDS.issubset(conflict_ids):
        missing = _REQUIRED_CONFLICT_IDS - conflict_ids
        return UnsolvableResult(
            score=0.0,
            passed=False,
            impossible_detected=True,
            classification=classification,
            conflict_ids=conflict_ids,
            explanation_valid=False,
            error=f"CONFLICT missing: {sorted(missing)}",
        )

    # --- EXPLANATION must be >= 50 characters ---
    explanation_valid = len(explanation_raw.strip()) >= 50

    if not explanation_valid:
        return UnsolvableResult(
            score=0.0,
            passed=False,
            impossible_detected=True,
            classification=classification,
            conflict_ids=conflict_ids,
            explanation_valid=False,
            error=f"EXPLANATION too short ({len(explanation_raw.strip())} chars)",
        )

    # --- All checks passed ---
    return UnsolvableResult(
        score=1.0,
        passed=True,
        impossible_detected=True,
        classification=classification,
        conflict_ids=conflict_ids,
        explanation_valid=True,
    )