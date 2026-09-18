"""Unit tests for the deterministic Context corpus (builder + scoring).

Covers the locked-contract invariants:

* fixed, known fact set with stable expected values
* prompt reaches the requested context size and embeds the facts block
* deterministic filler salt varies per run (cache-bust) without changing facts
* exact-match scoring; None (not 0) when nothing is gradeable
* degradation/retention math and the divide-by-zero guard
* unsupported/gap handling never coerces to zero
"""

from __future__ import annotations

import pytest

from src.context_corpus import (
    CONTEXT_POINTS,
    CONTEXT_POINT_LABELS,
    NUM_FACTS,
    build_facts_block,
    build_prompt,
    facts,
    requested_fact_keys,
)
from src.context_corpus import scoring as s


def _wc(text: str) -> int:
    """Deterministic token proxy used by the builder in tests."""
    return max(1, len(text.split()))


# ---------------------------------------------------------------------------
# Facts / contract identity
# ---------------------------------------------------------------------------

def test_fact_set_is_fixed_and_known():
    fs = facts()
    assert len(fs) == NUM_FACTS
    keys = [k for k, _ in fs]
    assert keys == requested_fact_keys()
    # Every value is single-line and free of newlines so exact-match scoring is unambiguous.
    for _key, value in fs:
        assert "\n" not in value and value.strip()


def test_facts_are_stable_across_calls():
    assert facts() == facts()


def test_context_points_match_roadmap_values():
    assert CONTEXT_POINTS == (15000, 30000, 60000, 120000, 180000, 240000)
    assert all(CONTEXT_POINT_LABELS[p] == f"{p // 1000}K" for p in CONTEXT_POINTS)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def test_build_prompt_embeds_facts_block_and_marker():
    prompt = build_prompt(2000, filler_salt="a", token_count=_wc)
    assert "CONTEXT_BENCHMARK_INPUT" in prompt
    for line in build_facts_block().splitlines():
        assert line in prompt


def test_build_prompt_reaches_requested_size():
    # word-count proxy: built prompt must be at/just above ~0.95 * target words
    prompt = build_prompt(4000, filler_salt="a", token_count=_wc)
    words = len(prompt.split())
    assert words >= int(4000 * 0.95)


def test_build_prompt_rejects_non_positive_target():
    with pytest.raises(ValueError):
        build_prompt(0, filler_salt="a", token_count=_wc)
    with pytest.raises(ValueError):
        build_prompt(-5, filler_salt="a", token_count=_wc)


def test_filler_salt_varies_content_but_not_facts():
    p1 = build_prompt(1500, filler_salt="run-1", token_count=_wc)
    p2 = build_prompt(1500, filler_salt="run-2", token_count=_wc)
    # Facts block identical (gradeable content is stable across runs).
    assert build_facts_block() in p1 and build_facts_block() in p2
    # Filler differs per salt (defeats cross-run KV-cache reuse).
    assert p1 != p2


# ---------------------------------------------------------------------------
# Scoring math
# ---------------------------------------------------------------------------

def test_point_score_perfect_and_partial():
    assert s.point_score(12, 12) == 1.0
    assert s.point_score(10, 12) == pytest.approx(0.833333)


def test_point_score_none_when_nothing_gradeable_not_zero():
    assert s.point_score(0, 0) is None
    assert s.point_score(5, 0) is None


def test_point_score_rejects_bad_input():
    with pytest.raises(ValueError):
        s.point_score(-1, 5)
    with pytest.raises(ValueError):
        s.point_score(6, 5)


def test_degradation_and_retention_math():
    assert s.degradation_from_baseline(0.8, 1.0) == -0.2
    assert s.retention_relative_to_baseline(0.8, 1.0) == 0.8
    # missing inputs -> None (never fabricated)
    assert s.degradation_from_baseline(None, 1.0) is None
    assert s.retention_relative_to_baseline(0.5, None) is None


def test_retention_divide_by_zero_guard():
    # baseline zero must NOT yield a spurious ratio
    assert s.retention_relative_to_baseline(0.5, 0) is None


def test_evaluate_fact_exact_match_and_normalization():
    passed, actual = s.evaluate_fact("F01", "blue lighthouse 1234", "blue lighthouse 1234\n")
    assert passed is True and actual == "blue lighthouse 1234"
    passed, actual = s.evaluate_fact("F01", "blue lighthouse 1234", "WRONG")
    assert passed is False
    # no answer at all -> None (distinct from an empty-string the model returned)
    passed, actual = s.evaluate_fact("F01", "x", None)
    assert passed is False and actual is None


def test_summarize_point_reports_score_and_evidence():
    records = [("F01", "a", "a", True), ("F02", "b", "wrong", False)]
    summary = s.summarize_point(records, status=s.STATUS_SUCCESS)
    assert summary["facts_requested"] == 2
    assert summary["facts_correct"] == 1
    assert summary["score"] == 0.5
    assert summary["failed_fact_keys"] == ["F02"]
    assert len(summary["evidence"]) == 2
