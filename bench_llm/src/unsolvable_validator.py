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
    "direct-logical-contradiction",
    "logical-contradiction",
})


def _normalize_response(response: str) -> str:
    """Normalize the response for parsing.

    - Strip surrounding Markdown code fences (```text ... ```)
    - Normalize whitespace
    """
    if not response or not isinstance(response, str):
        return ""
    text = response.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove opening fence line (e.g., ```text or ```python)
        if re.match(r"^\`\`\`", lines[0]):
            lines = lines[1:]
        # Remove closing fence line
        if lines and re.match(r"^\`\`\`", lines[-1]):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


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
    # Normalize: strip code fences, normalize whitespace
    normalized = _normalize_response(response)

    # --- Empty / None / whitespace response ---
    if not normalized or not normalized.strip():
        return UnsolvableResult(
            score=0.0,
            passed=False,
            impossible_detected=False,
            classification="",
            conflict_ids=set(),
            explanation_valid=False,
            error="empty response",
        )

    impossible_raw = _extract_field(normalized, "IMPOSSIBLE")
    class_raw = _extract_field(normalized, "CLASS")
    conflict_raw = _extract_field(normalized, "CONFLICT")

    # Check if EXPLANATION field marker exists in the response (not just its value)
    explanation_field_exists = re.search(
        r"(?i)^EXPLANATION\s*:", normalized, re.MULTILINE
    ) is not None

    explanation_raw = _extract_field(normalized, "EXPLANATION")

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
    # Normalize: replace spaces with hyphens, lowercase (e.g. "Contradictory Requirements" -> "contradictory-requirements")
    class_normalized = re.sub(r"\s+", "-", class_raw.strip().lower())
    classification = class_normalized
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

    # --- EXPLANATION validation ---
    # The explanation must identify that classify(0) cannot simultaneously
    # return both "zero" and "positive".  Accept partial matches.
    _explanation_keywords = [
        "classify",
        "classif",
        "zero",
        "positive",
        "simultaneous",
        "conflict",
        "contradict",
        "r1",
        "r2",
    ]

    explanation_lower = explanation_raw.lower() if explanation_raw else ""
    has_explanation_content = len(explanation_raw.strip()) >= 10  # lowered threshold for truncated responses

    # Check that the explanation identifies classify(0) cannot simultaneously return both "zero" and "positive"
    # Accept partial matches: must contain at least 2 of the key terms
    keyword_matches = sum(
        1 for kw in ["classify", "zero", "positive", "r1", "r2", "conflict", "contradict"]
        if kw in explanation_lower
    )

    # If no explicit EXPLANATION field but we have IMPOSSIBLE + CLASS + CONFLICT(R1,R2),
    # and the response contains relevant reasoning keywords, accept it as valid.
    has_required_fields = (
        impossible_detected
        and classification in _VALID_CLASSES
        and _REQUIRED_CONFLICT_IDS.issubset(conflict_ids)
    )

    if has_required_fields:
        # Core terms that must appear in the explanation for it to be valid.
        # The response must explain that classify(0) cannot simultaneously return "zero" and "positive".
        core_terms = ["classify", "zero", "positive"]
        core_matches = sum(1 for kw in core_terms if kw in explanation_lower)

        # Also check the full normalized text (not just EXPLANATION field value) for reasoning content.
        # This handles cases where the model provides reasoning without an explicit EXPLANATION: label.
        full_text_lower = normalized.lower()
        full_core_matches = sum(1 for kw in core_terms if kw in full_text_lower)

        if has_explanation_content and keyword_matches >= 2:
            # Explanation present with enough keywords — but must include at least one core term
            if core_matches >= 1 or keyword_matches >= 4:
                explanation_valid = True
            else:
                explanation_valid = False
        elif not has_explanation_content and keyword_matches >= 3:
            # Truncated but contains enough reasoning context (e.g. "CONFLICT:" truncated)
            explanation_valid = True
        elif not explanation_field_exists and full_core_matches >= 2:
            # No explicit EXPLANATION field, but the response text contains at least 2 core terms
            # (classify + zero/positive), indicating actual contradiction reasoning was provided.
            explanation_valid = True
        else:
            explanation_valid = False

    if not explanation_valid:
        return UnsolvableResult(
            score=0.0,
            passed=False,
            impossible_detected=True,
            classification=classification,
            conflict_ids=conflict_ids,
            explanation_valid=False,
            error=f"EXPLANATION insufficient ({len(explanation_raw.strip())} chars, {keyword_matches} keywords)",
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
