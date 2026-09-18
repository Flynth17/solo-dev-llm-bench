"""Deterministic corpus construction + scoring for the Context benchmark family.

This package is the *locked benchmark-contract* layer for :mod:`RM-26-AA-0009`
(``Context benchmark family``). It answers exactly one question, in a fully
deterministic, no-LLM-judge way:

    "What happens to a model's *correctness/retention* as the same task is
     evaluated with increasing effective context?"

It is deliberately NOT:

* Standard Speed (throughput / TTFT / prefill timing),
* Workflow / Agentic (multi-suite developer correctness),
* AI Intelligence (discriminative reasoning quality),
* any composite / overall score.

Design contract (see :mod:`src.v2_context_suite_runner` for the authoritative
statement):

* **Facts are fixed and known.** A stable set of keyed facts with deterministic
  expected values is embedded in every prompt, so scoring is exact-string
  comparison against values we already know -- no judge, no ambiguity.
* **Context grows via deterministic filler.** Only *filler* scales with the
  requested context point; the gradeable facts never change, so points stay
  comparable across sizes, runs and models.
* **Unsupported is a gap, never zero.** A point above the model's effective
  capacity is persisted as ``unsupported`` with no score -- it is never coerced
  to numeric zero and never rescaled to hide it.

Everything here is pure and side-effect free so it can be unit tested in
isolation without an LLM endpoint, without touching disk and without writing to
any active dataset.
"""

from __future__ import annotations

from .builder import (
    CONTEXT_POINTS,
    CONTEXT_POINT_LABELS,
    NUM_FACTS,
    FACTOR_UNDERSHOOT,
    build_facts_block,
    build_prompt,
    facts,
    requested_fact_keys,
)
from .scoring import (
    STATUS_SUCCESS,
    STATUS_EXTRACTION_FAILURE,
    STATUS_MALFORMED,
    STATUS_UNSUPPORTED,
    STATUS_FAILED,
    point_score,
    degradation_from_baseline,
    retention_relative_to_baseline,
    summarize_point,
)

__all__ = [
    "CONTEXT_POINTS",
    "CONTEXT_POINT_LABELS",
    "NUM_FACTS",
    "FACTOR_UNDERSHOOT",
    "build_facts_block",
    "build_prompt",
    "facts",
    "requested_fact_keys",
    "STATUS_SUCCESS",
    "STATUS_EXTRACTION_FAILURE",
    "STATUS_MALFORMED",
    "STATUS_UNSUPPORTED",
    "STATUS_FAILED",
    "point_score",
    "degradation_from_baseline",
    "retention_relative_to_baseline",
    "summarize_point",
]
