"""Canonical evaluation speed prompts.

Backend-owned fixed prompts for speed-benchmarking three different workload sizes.
Prompts are stored as Markdown fixture files under ``tasks/speed_prompts/``.

Prompt mapping (name -> fixture file):
    small  -> small.md   (~267 tokens)
    medium -> medium.md  (~1,359 tokens)
    large  -> large.md   (~5,318 tokens)
"""

from __future__ import annotations

import os
from typing import Dict

# ------------------------------------------------------------------
# Fixture file mapping
# ------------------------------------------------------------------

PROMPT_FILES: Dict[str, str] = {
    "small": "small.md",
    "medium": "medium.md",
    "large": "large.md",
}

# ------------------------------------------------------------------
# Token estimation helper
# ------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Rough token-count estimate for *text*.

    Uses a simple characters-per-token heuristic.  The standard approximation
    for many tokenizers (GPT-2, CL100K, etc.) is roughly **4 characters per
    token** for English text, though the exact ratio varies by model.

    This function is intentionally lightweight -- no heavyweight tokenizer
    dependency is required.

    Args:
        text: The string to estimate tokens for.

    Returns:
        An approximate token count (always >= 1 for non-empty input).
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


# ------------------------------------------------------------------
# Fixture loader
# ------------------------------------------------------------------

def _load_prompt(name: str) -> str:
    """Load the speed prompt fixture for *name* from disk.

    Args:
        name: One of ``"small"``, ``"medium"``, or ``"large"``.

    Returns:
        The prompt text.

    Raises:
        ValueError: If the name is unknown or the file cannot be read.
    """
    filename = PROMPT_FILES.get(name)
    if filename is None:
        raise ValueError(
            f"Unknown evaluation speed prompt: {name!r}. "
            f"Expected one of {sorted(PROMPT_FILES)}."
        )

    # Resolve relative to this package's directory
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(pkg_dir)
    fixture_path = os.path.join(project_root, "tasks", "speed_prompts", filename)

    if not os.path.isfile(fixture_path):
        raise FileNotFoundError(
            f"Speed prompt fixture not found for {name!r}: {fixture_path}"
        )

    with open(fixture_path, "r", encoding="utf-8") as fh:
        return fh.read()


# ------------------------------------------------------------------
# Cached prompts (loaded on first access)
# ------------------------------------------------------------------

_SPEED_PROMPTS_CACHE: Dict[str, str] | None = None


def _get_speed_prompts() -> Dict[str, str]:
    """Return a cached dict of all speed prompts, loading from fixtures once."""
    global _SPEED_PROMPTS_CACHE
    if _SPEED_PROMPTS_CACHE is None:
        _SPEED_PROMPTS_CACHE = {}
        for name in PROMPT_FILES:
            _SPEED_PROMPTS_CACHE[name] = _load_prompt(name)
    return _SPEED_PROMPTS_CACHE


# Public accessor -- use this instead of direct dict access
SPEED_PROMPTS: Dict[str, str] = _get_speed_prompts()


def get_speed_prompt(name: str) -> str:
    """Return the canonical speed prompt for the given size label.

    Prompts are loaded from Markdown fixture files on first call and cached
    thereafter.

    Args:
        name: One of ``"small"``, ``"medium"``, or ``"large"``.

    Returns:
        The prompt string.

    Raises:
        ValueError: If *name* is not a recognized prompt label.
    """
    if name not in PROMPT_FILES:
        raise ValueError(
            f"Unknown evaluation speed prompt: {name!r}. "
            f"Expected one of {sorted(PROMPT_FILES)}."
        )
    # Ensure cache is populated
    _get_speed_prompts()
    return SPEED_PROMPTS[name]