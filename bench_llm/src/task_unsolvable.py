"""Unsolvable (contradictory-requirements) LLM task runner for Solo Dev LLM Bench.

Loads the human-editable prompt.md + scenario.md fixtures, sends the combined
prompt to an LM Studio endpoint, normalizes the model output, and validates it
deterministically using unsolvable_validator.py.

Architecture:

    tasks/unsolvable/  = benchmark content (prompt.md, scenario.md)
    src/               = execution + scoring (task_unsolvable.py, unsolvable_validator.py)

Expected structured response::

    IMPOSSIBLE: yes
    CLASS: contradictory-requirements
    CONFLICT: R1, R2
    EXPLANATION: <at least 50 characters of reasoning>

Correct result: score=1.0, passed=True
Anything else: score=0.0, passed=False
"""

from __future__ import annotations

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


# ------------------------------------------------------------------
# Fixture loading
# ------------------------------------------------------------------

def _load_fixture(filename: str) -> str:
    """Load a fixture file from the unsolvable fixture directory."""
    path = _FIXTURE_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Unsolvable fixture not found: {path}")
    return path.read_text(encoding="utf-8")


def load_prompt() -> str:
    """Load the human-editable prompt from the task fixture directory."""
    return _load_fixture("prompt.md")


def load_scenario() -> str:
    """Load the scenario definition from the task fixture directory."""
    return _load_fixture("scenario.md")


# ------------------------------------------------------------------
# Prompt construction
# ------------------------------------------------------------------

def build_unsolvable_prompt() -> str:
    """Build the final model prompt from prompt.md + scenario.md.

    The prompt.md template uses ``{{scenario}}`` as a placeholder that is
    replaced with the full scenario content.
    """
    prompt_template = load_prompt()
    scenario = load_scenario()

    if "{{scenario}}" in prompt_template:
        return prompt_template.replace("{{scenario}}", scenario)

    # Fallback: append scenario if no placeholder
    return f"{prompt_template}\n\n---\n\n{scenario}"


# ------------------------------------------------------------------
# Output normalization
# ------------------------------------------------------------------

def normalize_llm_output(raw_output: Any) -> str:
    """Normalize LM Studio /api/v1/chat output to a string.

    Handles:
    - string (direct text)
    - dict (e.g. {"output": "..."})
    - list (e.g. [{"content": "..."}] or ["text1", "text2"])
    """
    if isinstance(raw_output, str):
        return raw_output

    if isinstance(raw_output, dict):
        # Try common key names in priority order
        for key in ("output", "text", "response", "content"):
            if key in raw_output:
                val = raw_output[key]
                if isinstance(val, str):
                    return val
                # Nested dict with text
                if isinstance(val, dict) and "text" in val:
                    return val["text"]
        # Fallback: join all string values
        parts = []
        for v in raw_output.values():
            if isinstance(v, str):
                parts.append(v)
        return "\n".join(parts) if parts else str(raw_output)

    if isinstance(raw_output, (list, tuple)):
        parts = []
        for item in raw_output:
            if isinstance(item, dict):
                parts.append(str(item.get("content", "")))
            else:
                parts.append(str(item))
        return "".join(parts)

    return str(raw_output)


# ------------------------------------------------------------------
# Result dataclass
# ------------------------------------------------------------------

@dataclass
class UnsolvableCorrectnessResult:
    """Result of the unsolvable correctness task."""

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
    execution_environment: str
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
            "execution_environment": self.execution_environment,
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
    hardware_label: str = "",
    execution_environment: str = "Local",
    connection_type: str = "",
) -> UnsolvableCorrectnessResult:
    """Run the unsolvable (contradictory-requirements) task against an LM Studio endpoint.

    Args:
        lm_studio_url: LM Studio API URL.
        model: Model name to use.
        temperature: Sampling temperature.
        max_output_tokens: Maximum output tokens.
        hardware_label: Hardware label for reporting.
        execution_environment: Local / Self-hosted / Cloud.
        connection_type: Connection type (local/remote).

    Returns:
        UnsolvableCorrectnessResult with correctness + performance metadata.
    """
    import httpx

    # Build prompt
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

    start = time.perf_counter()
    ttft_seconds = 0.0

    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        elapsed = time.perf_counter() - start

    body = resp.json()
    stats = body.get("stats", {})

    # Extract output
    raw_output = body.get("output", body.get("text", body.get("response", "")))

    # Normalize output
    generated_response = normalize_llm_output(raw_output)

    # Extract stats
    input_tokens = stats.get("input_tokens", 0)
    output_tokens = stats.get("total_output_tokens", stats.get("output_tokens", 0))
    ttft = stats.get("time_to_first_token_seconds", 0)
    tps = stats.get("tokens_per_second", 0)

    # Validate with unsolvable_validator.py
    validator_result = validate_unsolvable_response(generated_response)

    # Compute performance metadata
    tokens_per_second = output_tokens / elapsed if elapsed > 0 else 0

    # Build result
    result = UnsolvableCorrectnessResult(
        task_name="unsolvable",
        task_type="unsolvable",
        model=model,
        score=validator_result.score,
        passed=validator_result.passed,
        impossible_detected=validator_result.impossible_detected,
        classification=validator_result.classification,
        conflict_ids=validator_result.conflict_ids,
        explanation_valid=validator_result.explanation_valid,
        output_tokens=output_tokens,
        input_tokens=input_tokens,
        tokens_per_second=round(tokens_per_second, 2),
        ttft_seconds=round(ttft, 4) if ttft else 0.0,
        wall_time_seconds=round(elapsed, 4),
        generated_response=generated_response,
        timestamp=datetime.now(timezone.utc).isoformat(),
        hardware_label=hardware_label,
        execution_environment=execution_environment,
        connection_type=connection_type,
        validator_result=validator_result,
    )

    return result