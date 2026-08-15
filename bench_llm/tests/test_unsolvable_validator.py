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