"""Java correctness LLM task runner for Solo Dev LLM Bench.

Loads the broken Solution.java fixture, sends it to an LLM via LM Studio,
strips the model's output, and validates it deterministically using java_validator.py.
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.java_validator import validate_java_solution, JavaValidationResult


# ------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tasks" / "java_correctness"


def _load_fixture(filename: str) -> str:
    """Load a fixture file from the java_correctness fixture directory."""
    path = _FIXTURE_DIR / filename
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------------------
# Prompt construction
# ------------------------------------------------------------------

def build_java_prompt() -> str:
    """Build the final model prompt from prompt.md + broken Solution.java."""
    prompt_template = _load_fixture("prompt.md")
    broken_code = _load_fixture("Solution.java")
    return prompt_template.format(broken_code)


# ------------------------------------------------------------------
# Output normalization
# ------------------------------------------------------------------

def _strip_fences(code: str) -> str:
    """Strip Markdown code fences (java / py / generic) from the output."""
    # Pattern: ```java ... ``` or ``` ... ```
    pattern = r"^```(?:java)?\s*\n(.*?)\n```\s*$"
    match = re.match(pattern, code, re.DOTALL | re.MULTILINE)
    if match:
        return match.group(1).strip()
    return code.strip()


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
class JavaCorrectnessResult:
    """Result of the Java correctness task."""
    task_name: str
    task_type: str
    model: str
    score: float
    passed: bool
    total_tests: int
    passed_tests: int
    failed_tests: int
    compile_success: bool
    output_tokens: int
    input_tokens: int
    tokens_per_second: float
    ttft_seconds: float
    wall_time_seconds: float
    generated_code: str
    timestamp: str
    hardware_label: str
    connection_type: str
    validator_result: JavaValidationResult = field(default=None)  # type: ignore

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage."""
        d = {
            "task_name": self.task_name,
            "task_type": self.task_type,
            "model": self.model,
            "score": self.score,
            "passed": self.passed,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "compile_success": self.compile_success,
            "output_tokens": self.output_tokens,
            "input_tokens": self.input_tokens,
            "tokens_per_second": self.tokens_per_second,
            "ttft_seconds": self.ttft_seconds,
            "wall_time_seconds": self.wall_time_seconds,
            "generated_code": self.generated_code,
            "timestamp": self.timestamp,
            "hardware_label": self.hardware_label,
            "connection_type": self.connection_type,
        }
        return d


# ------------------------------------------------------------------
# Task runner
# ------------------------------------------------------------------

async def run_java_correctness_task(
    lm_studio_url: str = "http://localhost:1234",
    model: str = "",
    temperature: float = 0.0,
    max_output_tokens: int = 1024,
    hardware_label: str = "local",
    connection_type: str = "local",
) -> JavaCorrectnessResult:
    """Run the Java correctness task against an LM Studio endpoint.

    Args:
        lm_studio_url: LM Studio API URL.
        model: Model name to use.
        temperature: Sampling temperature.
        max_output_tokens: Maximum output tokens.
        hardware_label: Hardware label for reporting.
        connection_type: Connection type (local/remote).

    Returns:
        JavaCorrectnessResult with correctness + performance metadata.
    """
    import httpx

    # Load fixtures and build prompt
    prompt = build_java_prompt()

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
    ttft_start = time.perf_counter()
    first_token_seen = False
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
    normalized = normalize_llm_output(raw_output)

    # Strip fences
    generated_code = _strip_fences(normalized)

    # Extract stats
    input_tokens = stats.get("input_tokens", 0)
    output_tokens = stats.get("total_output_tokens", stats.get("output_tokens", 0))
    tps = stats.get("tokens_per_second", 0)
    ttft = stats.get("time_to_first_token_seconds", 0)

    # Validate
    validator_result = validate_java_solution(generated_code)

    # Build result
    result = JavaCorrectnessResult(
        task_name="java_correctness",
        task_type="java_correctness",
        model=model,
        score=validator_result.score,
        passed=validator_result.passed,
        total_tests=validator_result.total_tests,
        passed_tests=validator_result.passed_tests,
        failed_tests=validator_result.failed_tests,
        compile_success=validator_result.compile_success,
        output_tokens=output_tokens,
        input_tokens=input_tokens,
        tokens_per_second=tps,
        ttft_seconds=ttft,
        wall_time_seconds=round(elapsed, 4),
        generated_code=generated_code,
        timestamp=datetime.now(timezone.utc).isoformat(),
        hardware_label=hardware_label,
        connection_type=connection_type,
        validator_result=validator_result,
    )

    return result