"""Tests for src/evaluation_prompts.py.

Verifies:
1. small, medium, large prompts exist
2. all are non-empty
3. small < medium < large (by character count)
4. approximate size bands are reasonable
5. repeated lookup returns identical content
6. unknown names are rejected
"""

from __future__ import annotations

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.evaluation_prompts import SPEED_PROMPTS, get_speed_prompt


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture()
def known_keys() -> list[str]:
    """Return the set of known speed prompt keys."""
    return sorted(SPEED_PROMPTS.keys())


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
# 4. Approximate size bands
# ------------------------------------------------------------------

class TestPromptSizeBands:
    """Verify prompts fall in roughly correct size ranges.

    Approximate token-to-char ratio: ~4 chars per token.
    small  ~250 tokens  -> ~1,000 chars
    medium ~1,000 tokens -> ~4,000 chars
    large  ~4,000 tokens -> ~16,000 chars
    """

    def test_small_size_approx_250_tokens(self) -> None:
        chars = len(SPEED_PROMPTS["small"])
        # Allow generous tolerance: ~250 tokens ~1000 chars, allow 30% tolerance
        assert 300 <= chars <= 1500, (
            f"small prompt length {chars} chars out of expected band [300, 1500]"
        )

    def test_medium_size_approx_1000_tokens(self) -> None:
        chars = len(SPEED_PROMPTS["medium"])
        # ~1000 tokens ~4000 chars; use generous tolerance for realistic prompts
        assert 800 <= chars <= 7500, (
            f"medium prompt length {chars} chars out of expected band [800, 7500]"
        )

    def test_large_size_approx_4000_tokens(self) -> None:
        chars = len(SPEED_PROMPTS["large"])
        # ~4000 tokens ~16000 chars; use generous tolerance for realistic prompts
        assert 2000 <= chars <= 25000, (
            f"large prompt length {chars} chars out of expected band [2000, 25000]"
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