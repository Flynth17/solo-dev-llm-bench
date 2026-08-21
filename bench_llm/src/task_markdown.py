"""Markdown benchmark task runner for Solo Dev LLM Bench.

Runs the Markdownlint Default benchmark:
1. Load the broken.md fixture
2. Send it to the LLM for correction
3. Validate with markdownlint
4. Score and save to Task History
"""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.markdownlint_validator import (
    MarkdownLintValidator,
    calculate_score,
    run_markdownlint_benchmark,
)

# ------------------------------------------------------------------
# Task definition
# ------------------------------------------------------------------

TASK_DEFINITION = {
    "name": "Markdownlint Default",
    "task_type": "markdown",
    "validator": "markdownlint",
    "max_output_tokens": 1024,
    "temperature": 0,
    "fixture_dir": "markdownlint_default",
}

# ------------------------------------------------------------------
# Fixture loading
# ------------------------------------------------------------------


def get_fixture_path(fixture_name: str) -> Path:
    """Get the path to a fixture file."""
    return Path(__file__).parent.parent / "tasks" / fixture_name


def load_fixture_broken_md(fixture_name: str = "markdownlint_default/broken.md") -> str:
    """Load the broken.md fixture content."""
    fixture_path = get_fixture_path(fixture_name)
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture not found: {fixture_path}")
    return fixture_path.read_text(encoding="utf-8")


# ------------------------------------------------------------------
# Prompt construction
# ------------------------------------------------------------------

def _load_prompt() -> str:
    """Load the human-editable prompt from the task fixture directory."""
    prompt_path = Path(__file__).parent.parent / "tasks" / "markdownlint_default" / "prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def build_benchmark_prompt(document: str) -> str:
    """Build the prompt for the markdown benchmark.

    Loads the human-editable prompt.md fixture and appends the document content.
    """
    instructions = _load_prompt()
    return f"{instructions}\n\n{document}"


# ------------------------------------------------------------------
# Runtime output persistence
# ------------------------------------------------------------------

def _save_latest_markdown_output(content: str) -> None:
    """Write the exact generated Markdown to runtime/markdown/latest_output.md.

    Creates parent directories if missing. Always overwrites (never appends).
    """
    output_dir = Path(__file__).parent.parent / "runtime" / "markdown"
    os.makedirs(output_dir, exist_ok=True)
    output_file = output_dir / "latest_output.md"
    output_file.write_text(content, encoding="utf-8")


# ------------------------------------------------------------------
# Output normalization (Act M4.6 — discard reasoning blocks)
# ------------------------------------------------------------------

def _extract_final_message(raw_output: Any) -> tuple[str | None, str]:
    """Extract the final answer from LM Studio response output.

    Uses the same strategy as Java task runner:
    - If raw_output is a list/tuple of chat blocks, scan from the END for
      the last block with type == "message" (the final answer).
    - Discard reasoning/thinking blocks entirely.
    - Return (final_message_content_or_None, failure_reason_or_empty_string).

    Returns:
        (content, reason) where:
          - content is the extracted final message string, or None if not found
          - reason is a failure code like "no_final_answer" or ""
    """
    # Use lstrip only: preserve trailing newlines (markdownlint MD047) while
    # removing any accidental leading blank lines from reasoning blocks.
    def _lstrip_only(s: str) -> str:
        return s.lstrip()

    if isinstance(raw_output, str):
        return _lstrip_only(raw_output), ""

    if isinstance(raw_output, dict):
        for key in ("output", "text", "response", "content"):
            if key in raw_output:
                val = raw_output[key]
                if isinstance(val, str):
                    return _lstrip_only(val), ""
                if isinstance(val, dict) and "text" in val:
                    return _lstrip_only(val["text"]), ""
        # Fallback: join all string values only
        parts = []
        for v in raw_output.values():
            if isinstance(v, str):
                parts.append(v)
        joined = "\n".join(parts)
        return (_lstrip_only(joined) or None), "no_final_answer"

    if isinstance(raw_output, (list, tuple)):
        # Scan from the END for the last block with type == "message"
        for item in reversed(list(raw_output)):
            if isinstance(item, dict) and item.get("type") == "message":
                content = item.get("content", "")
                if isinstance(content, str):
                    return _lstrip_only(content), ""
                # Content as list of parts (common with some LM Studio endpoints)
                if isinstance(content, (list, tuple)):
                    parts = [str(p) for p in content if isinstance(p, (str, int, float))]
                    joined = "\n".join(parts)
                    result = _lstrip_only(joined)
                    return result or None, "no_final_answer" if not result else ""

        # No "message" block found — model returned only reasoning/thinking
        return None, "no_final_answer"

    # Unknown type: treat as empty
    return None, "no_final_answer"


# ------------------------------------------------------------------
# Task runner
# ------------------------------------------------------------------

async def run_markdown_task(
    lm_studio_url: str,
    model: str,
    fixture_name: str = "markdownlint_default/broken.md",
    max_output_tokens: int = 1024,
    temperature: float = 0,
    hardware_label: str = "",
    execution_environment: str = "Local",
    connection_type: str = "",
) -> dict[str, Any]:
    """Run the Markdownlint Default benchmark task.

    Args:
        lm_studio_url: LM Studio server URL.
        model: Model key/identifier.
        fixture_name: Path to the fixture file.
        max_output_tokens: Maximum output tokens.
        temperature: Sampling temperature.
        hardware_label: Optional hardware label.
        execution_environment: Local / Self-hosted / Cloud.
        connection_type: Local network / Remote connection.

    Returns:
        Dict with task result compatible with Task Manager.
    """
    import httpx
    import time

    # Load fixture
    original_content = load_fixture_broken_md(fixture_name)

    # Build prompt
    prompt = build_benchmark_prompt(original_content)

    # Send to LLM
    url = f"{lm_studio_url}/api/v1/chat"
    payload = {
        "model": model,
        "input": prompt,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "stream": False,
        "store": False,
    }

    start_time = time.perf_counter()
    ttft = None

    async with httpx.AsyncClient(timeout=3000) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        body = resp.json()

    elapsed = time.perf_counter() - start_time

    # Validate canonical broken.md BEFORE checking for final model message.
    # This ensures initial_errors is always populated even on no_final_answer.
    validator = MarkdownLintValidator()
    dep_info = validator.check_dependency()
    validator_available = dep_info["cli_available"] or dep_info["python_available"]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()
    ) as f:
        f.write(original_content)
        f.flush()
        original_path = Path(f.name)

    try:
        original_result = validator.validate_file(original_path)
        initial_errors = original_result.count
    finally:
        original_path.unlink(missing_ok=True)

    stats = body.get("stats", {})

    # Capture TTFT from LM Studio response stats
    ttft = stats.get("time_to_first_token_seconds")

    # Extract output — use final message extraction (Act M4.6)
    raw_output = body.get("output", body.get("text", ""))
    generated_text, failure_reason = _extract_final_message(raw_output)

    # Output tokens
    output_tokens = stats.get("total_output_tokens", len(generated_text.split()) if generated_text else 0)
    input_tokens = stats.get("input_tokens", 0)

    # If no final message found, short-circuit with failure result.
    # initial_errors is already populated from the canonical fixture validation above.
    # CRITICAL: final_errors = initial_errors (not None) so benchmark result is complete/numeric.
    if not generated_text:
        tokens_per_second = output_tokens / elapsed if elapsed > 0 else 0
        return {
            "task_name": TASK_DEFINITION["name"],
            "task_type": TASK_DEFINITION["task_type"],
            "model": model,
            "initial_errors": initial_errors,
            "final_errors": initial_errors,
            "errors_fixed": 0,
            "score": 0.0,
            "passed": False,
            "output_tokens": output_tokens,
            "input_tokens": input_tokens,
            "tokens_per_second": round(tokens_per_second, 2),
            "ttft_seconds": round(ttft, 4) if ttft else None,
            "wall_time_seconds": round(elapsed, 4),
            "corrected_output": "",
            "corrected_violations": [],
            "dependency_message": dep_info["message"],
            "validator_available": validator_available,
            "failure_reason": failure_reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "hardware_label": hardware_label,
            "execution_environment": execution_environment,
            "connection_type": connection_type,
        }

    # Save exact generated output to runtime file for inspection.
    _save_latest_markdown_output(generated_text)

    # If no markdownlint implementation is available, short-circuit.
    if not validator_available:
        tokens_per_second = output_tokens / elapsed if elapsed > 0 else 0
        return {
            "task_name": TASK_DEFINITION["name"],
            "task_type": TASK_DEFINITION["task_type"],
            "model": model,
            "initial_errors": initial_errors,
            "final_errors": None,
            "errors_fixed": None,
            "score": None,
            "passed": False,
            "output_tokens": output_tokens,
            "input_tokens": input_tokens,
            "tokens_per_second": round(tokens_per_second, 2),
            "ttft_seconds": round(ttft, 4) if ttft else None,
            "wall_time_seconds": round(elapsed, 4),
            "corrected_output": generated_text,
            "corrected_violations": [],
            "dependency_message": dep_info["message"],
            "validator_available": False,
            "validator_error": (
                "markdownlint is not available: neither CLI nor python-markdownlint package found. "
                "Install via 'npm install -g markdownlint-cli' or 'pip install markdownlint'. "
                "Benchmark could not be evaluated."
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "hardware_label": hardware_label,
            "execution_environment": execution_environment,
            "connection_type": connection_type,
        }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()
    ) as f:
        f.write(generated_text)
        f.flush()
        corrected_path = Path(f.name)

    try:
        corrected_result = validator.validate_file(corrected_path)
        final_errors = corrected_result.count
        corrected_output = corrected_result.output
    finally:
        corrected_path.unlink(missing_ok=True)

    # Calculate score
    score_info = calculate_score(initial_errors, final_errors)

    # Compute tokens/sec
    tokens_per_second = output_tokens / elapsed if elapsed > 0 else 0

    return {
        "task_name": TASK_DEFINITION["name"],
        "task_type": TASK_DEFINITION["task_type"],
        "model": model,
        "initial_errors": initial_errors,
        "final_errors": final_errors,
        "errors_fixed": score_info["errors_fixed"],
        "score": score_info["score"],
        "passed": score_info["passed"],
        "output_tokens": output_tokens,
        "input_tokens": input_tokens,
        "tokens_per_second": round(tokens_per_second, 2),
        "ttft_seconds": round(ttft, 4) if ttft else None,
        "wall_time_seconds": round(elapsed, 4),
        "corrected_output": generated_text,
        "corrected_violations": corrected_result.violations,
        "dependency_message": dep_info["message"],
        "validator_available": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hardware_label": hardware_label,
        "execution_environment": execution_environment,
        "connection_type": connection_type,
    }