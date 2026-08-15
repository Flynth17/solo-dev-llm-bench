"""Unsolvable (contradictory-requirements) LLM task runner for Solo Dev LLM Bench.

Loads prompt.md + scenario.md, sends to LM Studio, extracts the final message
block (discarding reasoning/thinking blocks), and validates deterministically
using unsolvable_validator.py.
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.unsolvable_validator import validate_unsolvable_response, UnsolvableResult


# ------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tasks" / "unsolvable"


def _load_fixture(filename: str) -> str:
    """Load a fixture file from the unsolvable fixture directory."""
    path = _FIXTURE_DIR / filename
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------------------
# Task definition
# ------------------------------------------------------------------

TASK_DEFINITION = {
    "name": "Unsolvable Recognition",
    "task_type": "unsolvable",
    "validator": "unsolvable",
    "max_output_tokens": 1024,
    "temperature": 0,
}


# ------------------------------------------------------------------
# Prompt construction
# ------------------------------------------------------------------

def build_unsolvable_prompt() -> str:
    """Build the final model prompt from prompt.md + scenario.md."""
    prompt_template = _load_fixture("prompt.md")
    scenario = _load_fixture("scenario.md")
    return prompt_template.replace("{{scenario}}", scenario)


# ------------------------------------------------------------------
# Output normalization — reasoning/message extraction
# ------------------------------------------------------------------

def normalize_llm_output(output: Any) -> str:
    """Normalize LM Studio output to a single string.

    Handles:
    - str → returned as-is (trimmed)
    - dict → extracted from 'output', 'text', or 'response' key
    - list/tuple of LM Studio chat blocks ({type, content}) → prefer the LAST
      block with type == "message" (the final answer), discarding reasoning
      blocks. Never serialize dict objects via str(dict).
    - list/tuple of strings → joined
    """
    if isinstance(output, str):
        return output.strip()

    if isinstance(output, dict):
        for key in ("output", "text", "response", "content"):
            if key in output:
                val = output[key]
                if isinstance(val, str):
                    return val.strip()
                # Nested dict with text
                if isinstance(val, dict) and "text" in val:
                    return val["text"].strip()
        # Fallback: join all string values only (never str(dict)).
        parts = []
        for v in output.values():
            if isinstance(v, str):
                parts.append(v)
        return "\n".join(parts).strip()

    if isinstance(output, (list, tuple)):
        # LM Studio chat format: list of {"type": ..., "content": ...} blocks.
        # Search from the END and prefer the LAST block with type == "message"
        # (the final answer), discarding reasoning/thinking blocks.
        for item in reversed(list(output)):
            if isinstance(item, dict) and item.get("type") == "message":
                content = item.get("content", "")
                if isinstance(content, str):
                    return content.strip()
                # Some endpoints emit content as a list of parts.
                if isinstance(content, (list, tuple)):
                    parts = [str(p) for p in content if isinstance(p, (str, int, float))]
                    return "\n".join(parts).strip()

        # No "message" block found: safe fallback that extracts textual content
        # only. Never serialize dict objects with str(dict). Preserves the old
        # list-of-strings behavior for non-reasoning responses.
        parts = []
        for item in output:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("content", "text", "output"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        parts.append(val)
                        break
        return "\n".join(parts).strip()

    # Last resort: stringify only non-dict scalars to avoid dict repr leakage.
    if isinstance(output, (int, float)):
        return str(output).strip()
    return ""


# ------------------------------------------------------------------
# Result dataclass
# ------------------------------------------------------------------

@dataclass
class UnsolvableResultData:
    """Result of the unsolvable task runner."""
    task_name: str
    task_type: str
    model: str
    score: float
    passed: bool
    impossible_detected: bool
    classification: str
    conflict_ids: set
    explanation_valid: bool
    output_tokens: int
    input_tokens: int
    tokens_per_second: float
    ttft_seconds: float
    wall_time_seconds: float
    generated_response: str
    timestamp: str
    hardware_label: str
    connection_type: str
    validator_result: UnsolvableResult = field(default=None)  # type: ignore

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage."""
        return {
            "task_name": self.task_name,
            "task_type": self.task_type,
            "model": self.model,
            "score": self.score,
            "passed": self.passed,
            "impossible_detected": self.impossible_detected,
            "classification": self.classification,
            "conflict_ids": sorted(self.conflict_ids),
            "explanation_valid": self.explanation_valid,
            "output_tokens": self.output_tokens,
            "input_tokens": self.input_tokens,
            "tokens_per_second": self.tokens_per_second,
            "ttft_seconds": self.ttft_seconds,
            "wall_time_seconds": self.wall_time_seconds,
            "generated_response": self.generated_response,
            "timestamp": self.timestamp,
            "hardware_label": self.hardware_label,
            "connection_type": self.connection_type,
        }


# ------------------------------------------------------------------
# Task runner
# ------------------------------------------------------------------

async def run_unsolvable_task(
    lm_studio_url: str = "http://localhost:1234",
    model: str = "",
    temperature: float = 0.0,
    max_output_tokens: int = 1024,
    hardware_label: str = "local",
    connection_type: str = "local",
) -> UnsolvableResultData:
    """Run the unsolvable recognition task against an LM Studio endpoint.

    Args:
        lm_studio_url: LM Studio API URL.
        model: Model name to use.
        temperature: Sampling temperature.
        max_output_tokens: Maximum output tokens.
        hardware_label: Hardware label for reporting.
        connection_type: Connection type (local/remote).

    Returns:
        UnsolvableResultData with correctness + performance metadata.
    """
    import httpx
    import time as _time

    # Load fixtures and build prompt
    prompt = build_unsolvable_prompt()

    # Call LM Studio
    url = f"{lm_studio_url}/api/v1/chat"
    payload = {
        "model": model,
        "input": prompt,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "stream": False,
        "store": False,
    }

    start = _time.perf_counter()

    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        elapsed = _time.perf_counter() - start

    body = resp.json()
    stats = body.get("stats", {})

    # Extract output
    raw_output = body.get("output", body.get("text", body.get("response", "")))

    # Normalize output — prefer final message block, discard reasoning
    normalized = normalize_llm_output(raw_output)

    # Extract stats
    input_tokens = stats.get("input_tokens", 0)
    output_tokens = stats.get("total_output_tokens", stats.get("output_tokens", 0))
    tps = stats.get("tokens_per_second", 0)
    ttft = stats.get("time_to_first_token_seconds", 0)

    # Validate response deterministically
    validator_result = validate_unsolvable_response(normalized)

    # Build result
    result = UnsolvableResultData(
        task_name=TASK_DEFINITION["name"],
        task_type=TASK_DEFINITION["task_type"],
        model=model,
        score=validator_result.score,
        passed=validator_result.passed,
        impossible_detected=validator_result.impossible_detected,
        classification=validator_result.classification,
        conflict_ids=validator_result.conflict_ids,
        explanation_valid=validator_result.explanation_valid,
        output_tokens=output_tokens,
        input_tokens=input_tokens,
        tokens_per_second=tps,
        ttft_seconds=ttft,
        wall_time_seconds=round(elapsed, 4),
        generated_response=normalized,
        timestamp=datetime.now(timezone.utc).isoformat(),
        hardware_label=hardware_label,
        connection_type=connection_type,
        validator_result=validator_result,
    )

    return result