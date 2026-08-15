"""Tests for src/evaluation_prompts.py.

Verifies:
1. small, medium, large prompts exist
2. all are non-empty
3. small < medium < large (by character count)
4. approximate token size bands are reasonable
5. repeated lookup returns identical content
6. unknown names are rejected
7. fixture files exist and are non-empty
"""

from __future__ import annotations

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from src.evaluation_prompts import (
    SPEED_PROMPTS,
    get_speed_prompt,
    estimate_tokens,
    PROMPT_FILES,
)


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
# 4. Approximate token size bands (actual fixture sizes)
# ------------------------------------------------------------------

class TestPromptSizeBands:
    """Verify prompts fall in roughly correct token ranges.

    Actual fixture bands (based on chars/4 approximation):
        small:  225-325 tokens   (~1,070 chars)
        medium: 1,200-1,500 tokens  (~5,400 chars)
        large:  4,800-5,800 tokens  (~21,200 chars)
    """

    def test_small_size_bands(self) -> None:
        tokens = _token_count(SPEED_PROMPTS["small"])
        assert 225 <= tokens <= 325, (
            f"small prompt estimated at {tokens} tokens, "
            f"expected 225-325"
        )

    def test_medium_size_bands(self) -> None:
        tokens = _token_count(SPEED_PROMPTS["medium"])
        assert 1200 <= tokens <= 1500, (
            f"medium prompt estimated at {tokens} tokens, "
            f"expected 1200-1500"
        )

    def test_large_size_bands(self) -> None:
        tokens = _token_count(SPEED_PROMPTS["large"])
        assert 4800 <= tokens <= 5800, (
            f"large prompt estimated at {tokens} tokens, "
            f"expected 4800-5800"
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


# ------------------------------------------------------------------
# 7. Fixture file existence
# ------------------------------------------------------------------

class TestFixtureFiles:
    """Verify the Markdown fixture files exist and are non-empty."""

    def test_small_fixture_exists(self) -> None:
        path = self._fixture_path("small.md")
        assert os.path.isfile(path), f"Fixture file not found: {path}"

    def test_medium_fixture_exists(self) -> None:
        path = self._fixture_path("medium.md")
        assert os.path.isfile(path), f"Fixture file not found: {path}"

    def test_large_fixture_exists(self) -> None:
        path = self._fixture_path("large.md")
        assert os.path.isfile(path), f"Fixture file not found: {path}"

    def test_small_fixture_non_empty(self) -> None:
        path = self._fixture_path("small.md")
        assert os.path.getsize(path) > 0, "small.md fixture is empty"

    def test_medium_fixture_non_empty(self) -> None:
        path = self._fixture_path("medium.md")
        assert os.path.getsize(path) > 0, "medium.md fixture is empty"

    def test_large_fixture_non_empty(self) -> None:
        path = self._fixture_path("large.md")
        assert os.path.getsize(path) > 0, "large.md fixture is empty"

    def _fixture_path(self, filename: str) -> str:
        """Return the absolute path to a fixture file."""
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(pkg_dir)
        return os.path.join(project_root, "tasks", "speed_prompts", filename)