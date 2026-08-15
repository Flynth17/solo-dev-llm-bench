"""Python correctness task runner for Solo Dev LLM Bench.

Mirrors the existing Markdown task runner architecture:

1. Load the broken solution.py fixture
2. Build a repair prompt
3. Send the prompt to LM Studio /api/v1/chat
4. Normalize LM Studio output (string / dict / list)
5. Strip Markdown code fences if present
6. Validate with python_validator.py
7. Return deterministic correctness + performance metadata
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ------------------------------------------------------------------
# Task definition
# ------------------------------------------------------------------

TASK_DEFINITION = {
    "name": "Python Correctness",
    "task_type": "python",
    "validator": "pytest",
    "max_output_tokens": 1024,
    "temperature": 0,
    "fixture_dir": "python_correctness",
}

# ------------------------------------------------------------------
# Fixture loading
# ------------------------------------------------------------------

def get_fixture_path(fixture_name: str) -> Path:
    """Get the path to a fixture file."""
    return Path(__file__).parent.parent / "tasks" / fixture_name


def load_fixture_py(fixture_name: str = "python_correctness/solution.py") -> str:
    """Load the broken solution.py fixture content."""
    fixture_path = get_fixture_path(fixture_name)
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture not found: {fixture_path}")
    return fixture_path.read_text(encoding="utf-8")


# ------------------------------------------------------------------
# Prompt construction
# ------------------------------------------------------------------

BENCHMARK_PROMPT_TEMPLATE = """Fix the bugs in the Python code below.

Preserve the original meaning and functionality.
Preserve all public function names and signatures.

Return ONLY the complete corrected solution.py.
Do not explain your changes.
Do not return tests.
Do not modify test_solution.py.
Do not wrap the result in Markdown code fences.

Here is the code:

{code}"""


def build_benchmark_prompt(code: str) -> str:
    """Build the prompt for the Python correctness benchmark."""
    return BENCHMARK_PROMPT_TEMPLATE.format(code=code)


# ------------------------------------------------------------------
# LM Studio output normalization
# ------------------------------------------------------------------

def normalize_llm_output(raw_output: Any) -> str:
    """Normalize LM Studio /api/v1/chat output to a string.

    Handles:
    - string (direct text)
    - dict (e.g. {"content": "..."})
    - list (e.g. [{"content": "..."}] or ["text1", "text2"])
    """
    if isinstance(raw_output, str):
        return raw_output
    elif isinstance(raw_output, dict):
        return str(raw_output.get("content", str(raw_output)))
    elif isinstance(raw_output, list):
        parts = []
        for item in raw_output:
            if isinstance(item, dict):
                parts.append(str(item.get("content", "")))
            else:
                parts.append(str(item))
        return "".join(parts)
    else:
        return str(raw_output)


# ------------------------------------------------------------------
# Markdown fence stripping
# ------------------------------------------------------------------

def strip_code_fences(text: str) -> str:
    """Strip Markdown code fences if present.

    Handles:
    - ```python ... ```
    - ```py ... ```
    - ``` ... ```
    """
    stripped = text.strip()
    fence_patterns = ["```python", "```py", "```"]
    for fence in fence_patterns:
        if stripped.startswith(fence):
            # Remove opening fence
            stripped = stripped[len(fence):]
            # Split by closing fence
            if "```" in stripped:
                stripped = stripped.split("```", 1)[0]
            return stripped.strip()
    return stripped


# ------------------------------------------------------------------
# Task runner
# ------------------------------------------------------------------

async def run_python_correctness_task(
    lm_studio_url: str,
    model: str,
    fixture_name: str = "python_correctness/solution.py",
    max_output_tokens: int = 1024,
    temperature: float = 0,
    hardware_label: str = "",
    execution_environment: str = "Local",
    connection_type: str = "",
) -> dict[str, Any]:
    """Run the Python Correctness benchmark task.

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

    from src.python_validator import validate_python_solution

    # Load test fixture content
    test_fixture_path = get_fixture_path("python_correctness/test_solution.py")
    test_code = test_fixture_path.read_text(encoding="utf-8")

    # 1. Load broken fixture
    original_code = load_fixture_py(fixture_name)

    # 2. Build prompt
    prompt = build_benchmark_prompt(original_code)

    # 3. Send to LM Studio
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

    # 4. Normalize output
    raw_output = body.get("output", body.get("text", ""))
    generated_code = normalize_llm_output(raw_output)

    # 5. Strip Markdown fences
    generated_code = strip_code_fences(generated_code)

    # 6. Extract token stats
    stats = body.get("stats", {})
    output_tokens = stats.get("total_output_tokens", len(generated_code.split()))
    input_tokens = stats.get("input_tokens", 0)

    # 7. Validate with python_validator.py
    validation_result = validate_python_solution(generated_code, test_code)

    # 8. Compute performance metadata
    tokens_per_second = output_tokens / elapsed if elapsed > 0 else 0

    return {
        "task_name": TASK_DEFINITION["name"],
        "task_type": TASK_DEFINITION["task_type"],
        "model": model,
        "score": validation_result.score,
        "passed": validation_result.passed,
        "total_tests": validation_result.total_tests,
        "passed_tests": validation_result.passed_tests,
        "failed_tests": validation_result.failed_tests,
        "output_tokens": output_tokens,
        "input_tokens": input_tokens,
        "tokens_per_second": round(tokens_per_second, 2),
        "ttft_seconds": round(ttft, 4) if ttft else None,
        "wall_time_seconds": round(elapsed, 4),
        "generated_code": generated_code,
        "validator_error": validation_result.error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hardware_label": hardware_label,
        "execution_environment": execution_environment,
        "connection_type": connection_type,
    }