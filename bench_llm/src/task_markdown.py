"""Markdown benchmark task runner for Solo Dev LLM Bench.

Runs the Markdownlint Default benchmark:
1. Load the broken.md fixture
2. Send it to the LLM for correction
3. Validate with markdownlint
4. Score and save to Task History
"""

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

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        body = resp.json()

    elapsed = time.perf_counter() - start_time

    # Extract output
    # LM Studio /api/v1/chat may return "output" as a string or as a list
    # of message dicts. Normalize to string at this boundary.
    raw_output = body.get("output", body.get("text", ""))
    if isinstance(raw_output, list):
        # Extract text content from message objects
        generated_text = ""
        for item in raw_output:
            if isinstance(item, dict):
                generated_text += item.get("content", "")
            elif isinstance(item, str):
                generated_text += item
    elif isinstance(raw_output, dict):
        generated_text = raw_output.get("content", str(raw_output))
    else:
        generated_text = str(raw_output)
    stats = body.get("stats", {})

    # Output tokens
    output_tokens = stats.get("total_output_tokens", len(generated_text.split()))
    input_tokens = stats.get("input_tokens", 0)

    # Validate with markdownlint
    validator = MarkdownLintValidator()

    dep_info = validator.check_dependency()
    validator_available = dep_info["cli_available"] or dep_info["python_available"]

    # If no markdownlint implementation is available, short-circuit.
    if not validator_available:
        tokens_per_second = output_tokens / elapsed if elapsed > 0 else 0
        return {
            "task_name": TASK_DEFINITION["name"],
            "task_type": TASK_DEFINITION["task_type"],
            "model": model,
            "initial_errors": None,
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

    # Validate original
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

    # Validate corrected
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
