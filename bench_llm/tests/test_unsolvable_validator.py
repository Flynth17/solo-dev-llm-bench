"""Tests for src/unsolvable_validator.py.

Covers 12 scenarios:

 1. correct structured answer passes
 2. IMPOSSIBLE=no fails
 3. wrong classification fails
 4. only R1 identified fails
 5. only R2 identified fails
 6. R1+R2 identified passes
 7. empty explanation fails
 8. short explanation fails
 9. empty response fails
10. code-only response fails
11. case-insensitive markers work
12. additional harmless text does not break validation
"""

import pytest

from src.unsolvable_validator import validate_unsolvable_response, UnsolvableResult


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_response(
    impossible: str = "yes",
    class_: str = "contradictory-requirements",
    conflict: str = "R1, R2",
    explanation: str = "Requirement R1 demands classify(0) return zero while R2 demands it return positive, which is a direct contradiction that cannot be resolved in any consistent implementation.",
) -> str:
    """Build a minimal structured response."""
    return (
        f"IMPOSSIBLE: {impossible}\n"
        f"CLASS: {class_}\n"
        f"CONFLICT: {conflict}\n"
        f"EXPLANATION: {explanation}"
    )


# ------------------------------------------------------------------
# 1. Correct structured answer passes
# ------------------------------------------------------------------

class TestCorrectAnswer:
    def test_passes(self):
        resp = _make_response()
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True
        assert result.impossible_detected is True
        assert result.classification == "contradictory-requirements"
        assert "R1" in result.conflict_ids
        assert "R2" in result.conflict_ids
        assert result.explanation_valid is True


# ------------------------------------------------------------------
# 2. IMPOSSIBLE=no fails
# ------------------------------------------------------------------

class TestImpossibleNo:
    def test_fails(self):
        resp = _make_response(impossible="no")
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False
        assert result.impossible_detected is False


# ------------------------------------------------------------------
# 3. Wrong classification fails
# ------------------------------------------------------------------

class TestWrongClassification:
    def test_fails(self):
        resp = _make_response(class_="undecidable")
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False


# ------------------------------------------------------------------
# 4. Only R1 identified fails
# ------------------------------------------------------------------

class TestOnlyR1:
    def test_fails(self):
        resp = _make_response(conflict="R1")
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False


# ------------------------------------------------------------------
# 5. Only R2 identified fails
# ------------------------------------------------------------------

class TestOnlyR2:
    def test_fails(self):
        resp = _make_response(conflict="R2")
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False


# ------------------------------------------------------------------
# 6. R1+R2 identified passes
# ------------------------------------------------------------------

class TestR1AndR2:
    def test_passes(self):
        resp = _make_response(conflict="R1, R2")
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True
        assert "R1" in result.conflict_ids
        assert "R2" in result.conflict_ids


# ------------------------------------------------------------------
# 7. Empty explanation fails
# ------------------------------------------------------------------

class TestEmptyExplanation:
    def test_fails(self):
        resp = _make_response(explanation="")
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False
        assert result.explanation_valid is False


# ------------------------------------------------------------------
# 8. Short explanation fails (< 50 chars)
# ------------------------------------------------------------------

class TestShortExplanation:
    def test_fails(self):
        resp = _make_response(explanation="R1 and R2 clash.")
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False
        assert result.explanation_valid is False


# ------------------------------------------------------------------
# 9. Empty response fails
# ------------------------------------------------------------------

class TestEmptyResponse:
    def test_fails(self):
        result = validate_unsolvable_response("")
        assert result.score == 0.0
        assert result.passed is False
        assert result.error == "empty response"

    def test_whitespace_only_fails(self):
        result = validate_unsolvable_response("   \n\n  ")
        assert result.score == 0.0
        assert result.passed is False


# ------------------------------------------------------------------
# 10. Code-only response fails
# ------------------------------------------------------------------

class TestCodeOnly:
    def test_fails(self):
        resp = 'def classify(x):\n    return "zero"'
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False


# ------------------------------------------------------------------
# 11. Case-insensitive markers work
# ------------------------------------------------------------------

class TestCaseInsensitiveMarkers:
    def test_lowercase_impossible(self):
        resp = _make_response(impossible="YES")
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True

    def test_uppercase_impossible(self):
        resp = _make_response(impossible="Yes")
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True

    def test_alternative_classification(self):
        """Alternative valid classifications should pass."""
        for cls in ("contradictory", "self-contradictory", "logically-contradictory"):
            resp = _make_response(class_=cls)
            result = validate_unsolvable_response(resp)
            assert result.score == 1.0, f"Failed for class={cls}"
            assert result.passed is True, f"Failed for class={cls}"


# ------------------------------------------------------------------
# 12. Additional harmless text does not break validation
# ------------------------------------------------------------------

class TestAdditionalText:
    def test_prefix_text_ignored(self):
        resp = (
            "Here is my analysis:\n"
            + _make_response()
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True

    def test_suffix_text_ignored(self):
        resp = (
            _make_response()
            + "\n\nHope this helps!"
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True

    def test_extra_fields_ignored(self):
        resp = (
            _make_response()
            + "\nNOTE: this is a test"
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

class TestEdgeCases:
    def test_none_response(self):
        """None should be handled gracefully."""
        result = validate_unsolvable_response(None)  # type: ignore
        assert result.score == 0.0
        assert result.passed is False

    def test_conflict_with_extra_text(self):
        """CONFLICT: R1 and R2 are contradictory should still match."""
        resp = _make_response(conflict="R1 and R2 are contradictory")
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True

    def test_conflict_case_insensitive_r(self):
        """R1/r1 should both match."""
        resp = _make_response(conflict="r1, r2")
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert "R1" in result.conflict_ids
        assert "R2" in result.conflict_ids


# ------------------------------------------------------------------
# U1.5 — Focused tests for updated unsolvable validator
# ------------------------------------------------------------------

class TestU15CanonicalExactFormat:
    """Test canonical exact format passes."""

    def test_canonical_exact_format_passes(self):
        """Canonical exact format should PASS with score 1.0."""
        resp = (
            "IMPOSSIBLE: yes\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1, R2\n"
            "EXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive, which is a direct contradiction that cannot be resolved in any consistent implementation."
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True
        assert result.impossible_detected is True
        assert "R1" in result.conflict_ids
        assert "R2" in result.conflict_ids
        assert result.explanation_valid is True


class TestU15QwenRealFormat:
    """Test Qwen's real output format — U1.6 requires contradiction explanation."""

    def test_qwen_real_format_fails_no_explanation(self):
        """Qwen's truncated format without contradiction reasoning should FAIL (U1.6)."""
        resp = (
            '```\n'
            'IMPOSSIBLE: yes\n'
            'CLASS: Contradictory Requirements\n'
            'CONFLICT: R1, R2\n'
            '```'
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False
        assert result.impossible_detected is True

    def test_qwen_real_format_lowercase_class_fails(self):
        """Qwen's format with lowercase class but no explanation should FAIL (U1.6)."""
        resp = (
            '```\n'
            'IMPOSSIBLE: yes\n'
            'CLASS: contradictory-requirements\n'
            'CONFLICT: R1,R2\n'
            '```'
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False

    def test_qwen_real_format_with_explanation_passes(self):
        """Qwen's format WITH contradiction explanation should PASS."""
        resp = (
            '```\n'
            'IMPOSSIBLE: yes\n'
            'CLASS: Contradictory Requirements\n'
            'CONFLICT: R1, R2\n'
            'EXPLANATION: classify(0) cannot simultaneously return "zero" and "positive".\n'
            '```'
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True


class TestU15WrongConflictIds:
    """Test wrong conflict IDs fail."""

    def test_wrong_conflict_ids_fail(self):
        """CONFLICT with R3, R4 should FAIL."""
        resp = (
            "IMPOSSIBLE: yes\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R3, R4\n"
            "EXPLANATION: This is a contradiction that cannot be resolved."
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False

    def test_only_r1_conflict_fail(self):
        """CONFLICT with only R1 should FAIL."""
        resp = (
            "IMPOSSIBLE: yes\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1\n"
            "EXPLANATION: This is a contradiction that cannot be resolved."
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False


class TestU15SaysPossible:
    """Test says possible fails."""

    def test_says_possible_fails(self):
        """IMPOSSIBLE: no should FAIL regardless of other fields."""
        resp = (
            "IMPOSSIBLE: no\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1, R2\n"
            "EXPLANATION: This is a contradiction that cannot be resolved."
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False
        assert result.impossible_detected is False

    def test_says_possible_lowercase_fails(self):
        """IMPOSSIBLE: Possible should FAIL."""
        resp = (
            "IMPOSSIBLE: possible\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1, R2\n"
            "EXPLANATION: This is a contradiction that cannot be resolved."
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False


class TestU15MissingInvalidExplanation:
    """Test missing/invalid explanation fails."""

    def test_no_explanation_field_fails(self):
        """No EXPLANATION field and no contradiction reasoning should FAIL (U1.6)."""
        resp = (
            "IMPOSSIBLE: yes\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1, R2\n"
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False

    def test_short_no_keyword_explanation_fails(self):
        """Short explanation with no relevant keywords should FAIL."""
        resp = (
            "IMPOSSIBLE: yes\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1, R2\n"
            "EXPLANATION: blah"
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False

    def test_truncated_without_explanation_fails(self):
        """Truncated response without contradiction reasoning should FAIL (U1.6)."""
        resp = (
            '```\n'
            'IMPOSSIBLE: yes\n'
            'CLASS: Contradictory Requirements\n'
            'CONFLICT: R1, R2\n'
            'CONFLICT:'  # truncated here, no explanation field
            '```'
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 0.0
        assert result.passed is False

    def test_truncated_with_explanation_passes(self):
        """Truncated response WITH contradiction explanation should PASS."""
        resp = (
            '```\n'
            'IMPOSSIBLE: yes\n'
            'CLASS: Contradictory Requirements\n'
            'CONFLICT: R1, R2\n'
            'EXPLANATION: classify(0) cannot return both "zero" and "positive".\n'
            '```'
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True


# ------------------------------------------------------------------
# U1.7 — Direct Logical Contradiction class
# ------------------------------------------------------------------

class TestU17DirectLogicalContradiction:
    """Test direct-logical-contradiction classification (U1.7)."""

    def test_direct_logical_contradiction_passes(self):
        """Exact proven response from the task spec should PASS with score 1.0."""
        resp = (
            "IMPOSSIBLE: yes\n"
            "CLASS: Direct Logical Contradiction\n"
            "CONFLICT: R1, R2\n"
            'EXPLANATION: classify(0) cannot simultaneously return "zero" and "positive".'
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True


# ------------------------------------------------------------------
# U1.5 — Code fence stripping tests (unchanged)
# ------------------------------------------------------------------

class TestU15CodeFenceStripping:
    """Test Markdown code fence stripping works."""

    def test_python_fence_stripped(self):
        """```python fences should be stripped."""
        resp = (
            '```python\n'
            "IMPOSSIBLE: yes\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1, R2\n"
            "EXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive.\n"
            '```'
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True

    def test_plain_fence_stripped(self):
        """Plain ``` fences should be stripped."""
        resp = (
            "```\n"
            "IMPOSSIBLE: yes\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1, R2\n"
            "EXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive.\n"
            "```"
        )
        result = validate_unsolvable_response(resp)
        assert result.score == 1.0
        assert result.passed is True

    def test_capitalization_independent(self):
        """Capitalisation differences should not affect validation."""
        for impossible_val in ["YES", "Yes", "yes"]:
            resp = (
                f"IMPOSSIBLE: {impossible_val}\n"
                "CLASS: contradictory-requirements\n"
                "CONFLICT: R1, R2\n"
                "EXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive."
            )
            result = validate_unsolvable_response(resp)
            assert result.score == 1.0, f"Failed for IMPOSSIBLE={impossible_val}"
            assert result.passed is True, f"Failed for IMPOSSIBLE={impossible_val}"
