"""Deterministic context/task builder for the Context benchmark family.

The builder turns a *requested* context point (in tokens) into a concrete prompt:

    CONTEXT_MARKER
    <deterministic filler sized to reach ~target tokens>
    <fixed block of keyed facts>
    <instruction asking for those facts by key>

Only the **filler** scales with the requested point. The gradeable facts are a
fixed, known set that never changes -- this is what keeps every context point
comparable across sizes, runs and models, and makes scoring an exact match
against values we already know (no LLM judge).

Token sizing uses an injectable ``token_count(text) -> int`` callable so the
pure builder stays unit-testable offline. The runner supplies a backend-aware
counter (one cheap calibration probe, then word-ratio estimation); tests supply
a deterministic stub. Whatever the counter, the runner records the *actual*
``input_tokens`` reported by the backend per point, so measured truth is never
replaced by an estimate.
"""

from __future__ import annotations

import random
from typing import Callable, Iterable, List, Optional

# ---------------------------------------------------------------------------
# Locked canonical identity (immutable contract).
# ---------------------------------------------------------------------------

# Requested context points in tokens -- the roadmap's "15K/30K/60K/120K/180K/240K
# where supported". These are *requested* targets; actual input_tokens are recorded
# per point and may differ (and below-capacity points are marked unsupported).
CONTEXT_POINTS: tuple[int, ...] = (15000, 30000, 60000, 120000, 180000, 240000)

CONTEXT_POINT_LABELS: dict[int, str] = {
    15000: "15K",
    30000: "30K",
    60000: "60K",
    120000: "120K",
    180000: "180K",
    240000: "240K",
}

# Number of fixed keyed facts embedded + requested at every point. Fixed so scores
# across points share the same denominator and stay comparable.
NUM_FACTS: int = 12

# Filler is grown until the prompt reaches at least this fraction of the target,
# so we land *at or just above* the requested point rather than consistently
# undershooting it.
FACTOR_UNDERSHOOT: float = 0.95

# Fixed seed for the gradeable facts. Same seed => same expected values on every
# run and for every model, which is what makes cross-model comparison meaningful.
_FACT_SEED: int = 0xC7E61F09

# Deterministic word bank used to generate reproducible filler lines. Kept small
# and fixed so filler is reconstructible from (seed, salt) without storing blobs.
_WORD_BANK: tuple[str, ...] = tuple(
    word
    for word in """alpha bravo charlie delta echo foxtrot golf hotel india juliet
    kilo lima mike november oscar papa quebec robert sierra tango uniform victor
    whiskey xray yankee zebra matrix vector vector quantum lattice prism cipher
    oracle nexus drift cadence orbit lattice meridian solstice equinox horizon""".split()
)


def _fact_value(index: int) -> str:
    """Return the deterministic expected value for fact *index* (0-based).

    Values are short, single-line and free of whitespace/newlines so exact-match
    scoring is unambiguous. Pure function of ``index`` -- identical everywhere.
    """
    rng = random.Random(_FACT_SEED ^ (0x9E3779B9 * (index + 1)))
    adj = rng.choice(("red", "blue", "fast", "quiet", "bright", "ancient", "silent", "golden"))
    noun = rng.choice(("lighthouse", "algorithm", "caravan", "observatory", "glacier", "engine"))
    code = rng.randint(1000, 9999)
    return f"{adj} {noun} {code}"


def facts() -> list[tuple[str, str]]:
    """Return the ordered ``(key, expected_value)`` pairs for every fact.

    Part of the public contract -- :func:`summarize_point` and tests read the
    same authoritative values rather than re-deriving them.
    """
    return [(f"F{i:02d}", _fact_value(i)) for i in range(NUM_FACTS)]


def requested_fact_keys() -> list[str]:
    """Return the ordered fact keys the request asks for (all of them)."""
    return [key for key, _ in facts()]


def build_facts_block() -> str:
    """Return the literal block containing every fact, one per line, in order.

    This is embedded verbatim in every prompt so the model has something concrete
    to retrieve; its content never changes between context points.
    """
    return "\n".join(f"{key}: {value}" for key, value in facts())


def _filler_line(rng: random.Random) -> str:
    """Return one deterministic filler line (carries no gradeable information)."""
    width = rng.randint(6, 10)
    return " ".join(rng.choice(_WORD_BANK) for _ in range(width))


def _word_count(text: str) -> int:
    return len(text.split())


def build_prompt(
    target_tokens: int,
    *,
    filler_salt: str,
    token_count: Callable[[str], int],
    include_marker: bool = True,
) -> str:
    """Build a prompt whose input size reaches ~``target_tokens`` tokens.

    Layout (facts always last, so they sink deeper as ``target_tokens`` grows --
    the position-sensitive degradation signal this benchmark measures):

        [CONTEXT_MARKER]            (optional stable anchor)
        <filler sized to target>
        <fixed facts block>
        <instruction for requested keys>

    ``filler_salt`` varies filler per run so repeated executions cannot reuse a
    cross-run KV cache hit; the *facts* stay fixed. ``token_count`` measures the
    growing prompt and is injectable for offline testing. Raises
    :class:`ValueError` if the target cannot be reached (e.g. counter returns 0).
    """
    if not isinstance(target_tokens, int) or isinstance(target_tokens, bool) or target_tokens <= 0:
        raise ValueError(f"target_tokens must be a positive integer, got {target_tokens!r}")

    sections: list[str] = []
    if include_marker:
        sections.append("CONTEXT_BENCHMARK_INPUT")

    # Grow filler until the prompt (filler + facts + instruction) reaches target.
    filler_iter = _iter_filler(filler_salt)
    instruction = (
        "Read the input above. Return each of the following keys with its exact "
        "value, one per line, in the format 'KEY: value'. Keys: "
        + ", ".join(requested_fact_keys())
    )
    facts_block = build_facts_block()

    # Start with a base filler batch and keep adding deterministic lines until we
    # reach the target. Guard against a non-measuring counter (infinite loop).
    batch = [next(filler_iter), next(filler_iter)]
    prompt = "\n".join([*sections, "\n".join(batch), facts_block])
    measured = token_count(prompt)
    target = max(1, int(target_tokens * FACTOR_UNDERSHOOT))
    guard = 0
    max_iterations = 10_000_000
    while measured < target:
        batch.extend(next(filler_iter) for _ in range(4))
        prompt = "\n".join([*sections, "\n".join(batch), facts_block])
        measured = token_count(prompt)
        guard += 1
        if guard > max_iterations:
            raise ValueError("context target unreachable with available filler")

    return prompt


def _iter_filler(salt: str):
    """Yield an unbounded stream of deterministic filler lines for *salt*."""
    seed_hash = abs(hash((salt, "context-filler"))) % (2**32)
    rng = random.Random(seed_hash)
    while True:
        yield _filler_line(rng)
