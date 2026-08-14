"""Markdown benchmark for Solo Dev LLM Bench.

Evaluates how well the model can produce correct, well-structured
markdown output within a token budget.  Measures:

- **Markdown Compliance** — does the output contain valid markdown?
- **Token Efficiency** — tokens/sec during markdown generation
- **Structure Quality** — presence of headings, lists, code blocks, emphasis
- **Completeness** — does the output match the expected structure?
"""

import re
import time
from typing import Any


# ------------------------------------------------------------------
# Scoring rubric
# ------------------------------------------------------------------

MARKDOWN_SCORE_WEIGHTS = {
    "headings": 0.25,
    "lists": 0.15,
    "code_blocks": 0.20,
    "emphasis": 0.10,
    "links": 0.10,
    "tables": 0.10,
    "horizontal_rule": 0.05,
    "blockquote": 0.05,
}

# Minimum structure requirements (pass threshold)
MIN_STRUCTURE_SCORE = 0.40        # 40 % of max score
MIN_TOKENS_PER_SEC = 5.0            # must generate at least 5 tok/s


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _score_headings(text: str) -> float:
    """Score presence of headings (ATX style: # ...)."""
    count = len(re.findall(r"^#{1,6}\s+", text, re.M))
    # 3+ headings is full marks
    return min(count / 3.0, 1.0)


def _score_lists(text: str) -> float:
    """Score presence of ordered / unordered lists."""
    unordered = len(re.findall(r"^\s*[-*+]\s+", text, re.M))
    ordered = len(re.findall(r"^\s*\d+\.\s+", text, re.M))
    total = unordered + ordered
    return min(total / 3.0, 1.0)


def _score_code_blocks(text: str) -> float:
    """Score presence of fenced code blocks."""
    count = len(re.findall(r"```", text))
    # Pairs of backticks indicate fenced blocks
    return min((count // 2) / 2.0, 1.0)


def _score_emphasis(text: str) -> float:
    """Score presence of bold / italic emphasis."""
    bold = len(re.findall(r"\*\*[^*]+\*\*", text))
    italic = len(re.findall(r"\*[^*]+\*", text))
    total = bold + italic
    return min(total / 3.0, 1.0)


def _score_links(text: str) -> float:
    """Score presence of markdown links [text](url)."""
    count = len(re.findall(r"\[[^\]]+\]\([^)]+\)", text))
    return min(count / 2.0, 1.0)


def _score_tables(text: str) -> float:
    """Score presence of simple pipe tables."""
    count = len(re.findall(r"\|[^|]+\|", text))
    # Need at least 2 rows + header
    return min((count // 2) / 3.0, 1.0)


def _score_horizontal_rule(text: str) -> float:
    """Score presence of horizontal rules (---, ***, ___)."""
    return 1.0 if re.search(r"^[-*_]{3,}$", text, re.M) else 0.0


def _score_blockquote(text: str) -> float:
    """Score presence of blockquotes ( > ...)."""
    return 1.0 if re.search(r"^>\s+", text, re.M) else 0.0


# Map of feature → scorer function
_SCORERS = {
    "headings": _score_headings,
    "lists": _score_lists,
    "code_blocks": _score_code_blocks,
    "emphasis": _score_emphasis,
    "links": _score_links,
    "tables": _score_tables,
    "horizontal_rule": _score_horizontal_rule,
    "blockquote": _score_blockquote,
}


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def evaluate_markdown(text: str) -> dict[str, Any]:
    """Evaluate markdown quality and return per-feature scores."""
    feature_scores = {}
    for feature, scorer in _SCORERS.items():
        feature_scores[feature] = round(scorer(text), 4)

    weighted = sum(
        feature_scores[f] * MARKDOWN_SCORE_WEIGHTS[f]
        for f in MARKDOWN_SCORE_WEIGHTS
    )
    return {
        "feature_scores": feature_scores,
        "overall_score": round(weighted, 4),
        "meets_minimum": weighted >= MIN_STRUCTURE_SCORE,
    }


async def run_markdown_benchmark(
    lm_studio_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    iterations: int = 3,
) -> dict:
    """Run the markdown benchmark against an LM Studio endpoint.

    Returns a dict compatible with ``ResultsStore.add_run`` rows.
    """
    import httpx
    url = f"{lm_studio_url}/api/v1/chat"
    payload = {
        "model": model,
        "input": prompt,
        "temperature": temperature,
        "max_output_tokens": max_tokens,
        "stream": False,
        "store": False,
    }

    runs = []
    for i in range(1, iterations + 1):
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            elapsed = time.perf_counter() - start

        body = resp.json()
        stats = body.get("stats", {})
        generated_text = body.get("output", body.get("text", ""))
        markdown_eval = evaluate_markdown(generated_text)

        runs.append({
            "iteration": i,
            "cold_or_warm": "cold" if i == 1 else "warm",
            "tokens_per_second": stats.get("tokens_per_second", 0),
            "ttft_seconds": stats.get("time_to_first_token_seconds", 0),
            "input_tokens": stats.get("input_tokens", 0),
            "output_tokens": stats.get("total_output_tokens", 0),
            "model_load_time_seconds": stats.get("model_load_time_seconds", None),
            "wall_time_seconds": round(elapsed, 4),
            # Markdown-specific fields
            "markdown_score": markdown_eval["overall_score"],
            "markdown_meets_minimum": markdown_eval["meets_minimum"],
            "generated_text_length": len(generated_text),
        })

    # Aggregate
    tps_values = [r["tokens_per_second"] for r in runs if r["tokens_per_second"] > 0]
    md_scores = [r["markdown_score"] for r in runs if r.get("markdown_score") is not None]

    aggregate = {}
    if tps_values:
        aggregate["avg_tokens_per_second"] = round(sum(tps_values) / len(tps_values), 2)
        aggregate["min_tokens_per_second"] = round(min(tps_values), 2)
        aggregate["max_tokens_per_second"] = round(max(tps_values), 2)

    if md_scores:
        aggregate["avg_markdown_score"] = round(sum(md_scores) / len(md_scores), 4)
        aggregate["min_markdown_score"] = round(min(md_scores), 4)
        aggregate["max_markdown_score"] = round(max(md_scores), 4)
        aggregate["markdown_meets_minimum"] = all(md_scores)

    return {
        "benchmark_type": "markdown",
        "aggregate": aggregate,
        "runs": runs,
    }


# ------------------------------------------------------------------
# Default prompts
# ------------------------------------------------------------------

DEFAULT_PROMPTS = [
    {
        "name": "Mixed Markdown Structure",
        "prompt": (
            "Write a short technical document about AI code assistants "
            "that includes: headings, an unordered list, an ordered list, "
            "a fenced code block with Python code, a table, a horizontal "
            "rule, and a blockquote. Keep it under 500 tokens."
        ),
    },
    {
        "name": "Recipe Format",
        "prompt": (
            "Write a recipe for chocolate chip cookies using markdown. "
            "Include a title (H1), ingredients list, numbered steps, "
            "a tip in a blockquote, and a horizontal rule before the "
            "nutrition facts table. Keep it under 400 tokens."
        ),
    },
    {
        "name": "API Documentation",
        "prompt": (
            "Write API documentation for a hypothetical '/api/v1/benchmark' "
            "endpoint. Include: H2 heading, request parameters table, "
            "a code block with a curl example, response format description "
            "with emphasis, and a link to a hypothetical changelog. "
            "Keep it under 450 tokens."
        ),
    },
]