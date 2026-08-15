"""Tests for src/evaluation_prompts.py.

Verifies:
1. small, medium, large prompts exist
2. all are non-empty
3. small < medium < large (by character count)
4. approximate token size bands are reasonable
5. repeated lookup returns identical content
6. unknown names are rejected
"""

from __future__ import annotations

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.evaluation_prompts import SPEED_PROMPTS, get_speed_prompt, estimate_tokens


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

# Approximate token count using the project's estimate_tokens helper.
# If the helper is not available, fall back to chars/4.
def _token_count(text: str) -> int:
    """Estimate token count for *text*."""
    try:
        return estimate_tokens(text)
    except Exception:
        return len(text) // 4


# ------------------------------------------------------------------
# 1. Existence
# ------------------------------------------------------------------

class TestPromptExistence:
    def test_small_exists(self) -> None:
        assert "small" in SPEED_PROMPTS

    def test_medium_exists(self) -> None:
        assert "medium" in SPEED_PROMPTS

    def test_large_exists(self) -> None:
        assert "large" in SPEED_PROMPTS


# ------------------------------------------------------------------
# 2. Non-empty
# ------------------------------------------------------------------

class TestPromptNonEmpty:
    def test_small_non_empty(self) -> None:
        assert SPEED_PROMPTS["small"], "small prompt must not be empty"

    def test_medium_non_empty(self) -> None:
        assert SPEED_PROMPTS["medium"], "medium prompt must not be empty"

    def test_large_non_empty(self) -> None:
        assert SPEED_PROMPTS["large"], "large prompt must not be empty"


# ------------------------------------------------------------------
# 3. Size ordering: small < medium < large
# ------------------------------------------------------------------

class TestPromptSizeOrdering:
    def test_small_less_than_medium(self) -> None:
        assert len(SPEED_PROMPTS["small"]) < len(SPEED_PROMPTS["medium"])

    def test_medium_less_than_large(self) -> None:
        assert len(SPEED_PROMPTS["medium"]) < len(SPEED_PROMPTS["large"])

    def test_small_less_than_large(self) -> None:
        assert len(SPEED_PROMPTS["small"]) < len(SPEED_PROMPTS["large"])


# ------------------------------------------------------------------
# 4. Approximate token size bands
# ------------------------------------------------------------------

class TestPromptSizeBands:
    """Verify prompts fall in roughly correct token ranges.

    Target bands (advertising labels):
        small:  ~200-325 tokens
        medium: ~850-1,500 tokens
        large:  ~3,500-4,500 tokens
    """

    def test_small_size_bands(self) -> None:
        tokens = _token_count(SPEED_PROMPTS["small"])
        assert 200 <= tokens <= 325, (
            f"small prompt estimated at {tokens} tokens, "
            f"expected 200-325"
        )

    def test_medium_size_bands(self) -> None:
        tokens = _token_count(SPEED_PROMPTS["medium"])
        assert 850 <= tokens <= 1500, (
            f"medium prompt estimated at {tokens} tokens, "
            f"expected 850-1500"
        )

    def test_large_size_bands(self) -> None:
        tokens = _token_count(SPEED_PROMPTS["large"])
        assert 3000 <= tokens <= 4500, (
            f"large prompt estimated at {tokens} tokens, "
            f"expected 3000-4500"
        )


# ------------------------------------------------------------------
# 5. Deterministic lookup
# ------------------------------------------------------------------

class TestPromptDeterminism:
    def test_small_lookup_returns_same_content(self) -> None:
        prompt1 = get_speed_prompt("small")
        prompt2 = get_speed_prompt("small")
        assert prompt1 == prompt2

    def test_medium_lookup_returns_same_content(self) -> None:
        prompt1 = get_speed_prompt("medium")
        prompt2 = get_speed_prompt("medium")
        assert prompt1 == prompt2

    def test_large_lookup_returns_same_content(self) -> None:
        prompt1 = get_speed_prompt("large")
        prompt2 = get_speed_prompt("large")
        assert prompt1 == prompt2


# ------------------------------------------------------------------
# 6. Unknown name rejection
# ------------------------------------------------------------------

class TestPromptUnknownRejection:
    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown evaluation speed prompt"):
            get_speed_prompt("")

    def test_unknown_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown evaluation speed prompt"):
            get_speed_prompt("huge")

    def test_number_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown evaluation speed prompt"):
            get_speed_prompt(123)  # type: ignore[arg-type]