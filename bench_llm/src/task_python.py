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

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ------------------------------------------------------------------
# Runtime output persistence (Act P5.1)
# ------------------------------------------------------------------

def _save_latest_python_output(content: str) -> None:
    """Write the exact submitted Python code to runtime/python/latest_output.py.

    Creates parent directories if missing. Always overwrites (never appends).
    This file contains ONLY the final submitted code — no reasoning/thinking text.
    """
    output_dir = Path(__file__).parent.parent / "runtime" / "python"
    os.makedirs(output_dir, exist_ok=True)
    output_file = output_dir / "latest_output.py"
    output_file.write_text(content, encoding="utf-8")


# ------------------------------------------------------------------
# Final message extraction (Act P5.1 — mirror Markdown's _extract_final_message)
# ------------------------------------------------------------------

def _extract_final_message(raw_output: Any) -> tuple[str | None, str]:
    """Extract the final answer from LM Studio response output.

    Uses the same strategy as Markdown task runner:
    - If raw_output is a list/tuple of chat blocks, scan from the END for
      the last block with type == "message" (the final answer).
    - Discard reasoning/thinking blocks entirely.
    - Return (final_message_content_or_None, failure_reason_or_empty_string).

    Returns:
        (content, reason) where:
          - content is the extracted final message string, or None if not found
          - reason is a failure code like "no_final_answer" or ""
    """
    if isinstance(raw_output, str):
        return raw_output.strip(), ""

    if isinstance(raw_output, dict):
        for key in ("output", "text", "response", "content"):
            if key in raw_output:
                val = raw_output[key]
                if isinstance(val, str):
                    return val.strip(), ""
                if isinstance(val, dict) and "text" in val:
                    return val["text"].strip(), ""
        # Fallback: join all string values only
        parts = []
        for v in raw_output.values():
            if isinstance(v, str):
                parts.append(v)
        return "\n".join(parts).strip() or None, "no_final_answer"

    if isinstance(raw_output, (list, tuple)):
        # Scan from the END for the last block with type == "message"
        for item in reversed(list(raw_output)):
            if isinstance(item, dict) and item.get("type") == "message":
                content = item.get("content", "")
                if isinstance(content, str):
                    return content.strip(), ""
                # Content as list of parts (common with some LM Studio endpoints)
                if isinstance(content, (list, tuple)):
                    parts = [str(p) for p in content if isinstance(p, (str, int, float))]
                    joined = "\n".join(parts).strip()
                    return joined or None, "no_final_answer" if not joined else ""

        # No "message" block found — model returned only reasoning/thinking
        return None, "no_final_answer"

    # Unknown type: treat as empty
    return None, "no_final_answer"


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

def _load_prompt() -> str:
    """Load the human-editable prompt from the task fixture directory."""
    prompt_path = Path(__file__).parent.parent / "tasks" / "python_correctness" / "prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def build_benchmark_prompt(code: str) -> str:
    """Build the prompt for the Python correctness benchmark.

    Loads the human-editable prompt.md fixture and appends the code content.
    """
    instructions = _load_prompt()
    return f"{instructions}\n\n{code}"


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


def _count_test_functions(test_code: str) -> int:
    """Count the number of test functions in a pytest test file.

    Looks for patterns like ``def test_`` at the start of a line or
    after indentation.  This provides a deterministic expected test
    count even when the test module cannot be collected.

    Args:
        test_code: The pytest test file content.

    Returns:
        The number of test functions found.
    """
    count = 0
    for line in test_code.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("def test_") and "(" in stripped:
            count += 1
    return count


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

    print("[PYTHON] START")

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        body = resp.json()

    elapsed = time.perf_counter() - start_time

    print("[PYTHON] LM_RESPONSE_RECEIVED")
    stats_raw = body.get("stats", {})
    print(f"  input_tokens: {stats_raw.get('input_tokens', 'N/A')}")
    print(f"  output_tokens: {stats_raw.get('total_output_tokens', 'N/A')}")

    # 4. Extract final message (Act P5.1 — discard reasoning blocks)
    raw_output = body.get("output", body.get("text", ""))
    generated_code, failure_reason = _extract_final_message(raw_output)

    print("[PYTHON] FINAL_MESSAGE_EXTRACTED")
    if generated_code:
        code_length = len(generated_code)
        add_fix = "YES" if ("a + b" in generated_code or "return a + b" in generated_code) else "NO"
        multiply_fix = "YES" if ("return result" in generated_code and "result = x * y" in generated_code) else "NO"
        is_even_fix = "YES" if ("n % 2 == 0" in generated_code or "== True" not in generated_code.split("is_even")[1].split("def ")[0][:300] if "is_even" in generated_code else "NO") else "NO"
        # More robust is_even fix check
        is_even_fix = "NO"
        for line in generated_code.splitlines():
            stripped = line.strip()
            if "is_even" in stripped and "def " not in stripped:
                continue
            if "n % 2 == 0" in stripped or "n%2==0" in stripped:
                is_even_fix = "YES"
                break
        print(f"  code_length: {code_length}")
        print(f"  contains add fix: {add_fix}")
        print(f"  contains multiply fix: {multiply_fix}")
        print(f"  contains is_even fix: {is_even_fix}")
    else:
        print(f"  code_length: 0")
        print(f"  failure_reason: {failure_reason}")

    # If no final message found, short-circuit with failure result.
    if not generated_code:
        stats = body.get("stats", {})
        output_tokens = stats.get("total_output_tokens", 0)
        input_tokens = stats.get("input_tokens", 0)

        # Count expected tests from test_code (deterministic for correctness benchmark)
        expected_tests = _count_test_functions(test_code)

        # Write empty runtime file when no final answer
        _save_latest_python_output("")

        tokens_per_second = output_tokens / elapsed if elapsed > 0 else 0
        print("[PYTHON] COMPLETE")
        print(f"  score: 0.0")
        print(f"  passed: False")
        print(f"  passed_tests: 0")
        print(f"  failed_tests: {expected_tests}")
        print(f"  total_tests: {expected_tests}")
        return {
            "task_name": TASK_DEFINITION["name"],
            "task_type": TASK_DEFINITION["task_type"],
            "model": model,
            "score": 0.0,
            "passed": False,
            "total_tests": expected_tests,
            "passed_tests": 0,
            "failed_tests": expected_tests,
            "output_tokens": output_tokens,
            "input_tokens": input_tokens,
            "tokens_per_second": round(tokens_per_second, 2),
            "ttft_seconds": round(ttft, 4) if ttft else None,
            "wall_time_seconds": round(elapsed, 4),
            "generated_code": "",
            "validator_error": failure_reason,
            "failure_reason": failure_reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "hardware_label": hardware_label,
            "execution_environment": execution_environment,
            "connection_type": connection_type,
        }

    # 5. Strip Markdown fences from the final message
    generated_code = strip_code_fences(generated_code)

    # 6. Save exact submitted code to runtime file (Act P5.1)
    _save_latest_python_output(generated_code)
    print("[PYTHON] RUNTIME_SAVED")

    # 7. Extract token stats
    stats = body.get("stats", {})
    output_tokens = stats.get("total_output_tokens", len(generated_code.split()))
    input_tokens = stats.get("input_tokens", 0)

    # 8. Validate with python_validator.py
    print("[PYTHON] VALIDATION_START")
    validation_result = validate_python_solution(generated_code, test_code)
    print("[PYTHON] VALIDATION_RESULT")
    print(f"  passed_tests: {validation_result.passed_tests}")
    print(f"  failed_tests: {validation_result.failed_tests}")
    print(f"  total_tests: {validation_result.total_tests}")
    print(f"  score: {validation_result.score}")
    print(f"  passed: {validation_result.passed}")
    print(f"  exit_code: {validation_result.exit_code}")
    if validation_result.stdout:
        print("  stdout:")
        for vline in validation_result.stdout.splitlines():
            print(f"    {vline}")
    if validation_result.stderr:
        print("  stderr:")
        for vline in validation_result.stderr.splitlines():
            print(f"    {vline}")

    # 9. Compute performance metadata
    tokens_per_second = output_tokens / elapsed if elapsed > 0 else 0

    # Build final result dict
    final_result = {
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
        # Preserved subprocess evidence (Act P5.12)
        "exit_code": validation_result.exit_code,
        "timed_out": validation_result.timed_out,
        "stdout": validation_result.stdout,
        "stderr": validation_result.stderr,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hardware_label": hardware_label,
        "execution_environment": execution_environment,
        "connection_type": connection_type,
    }

    # 10. Persist result
    print("[PYTHON] PERSISTING_RESULT")
    print(f"  score: {final_result['score']}")
    print(f"  passed: {final_result['passed']}")
    print(f"  passed_tests: {final_result['passed_tests']}")
    print(f"  failed_tests: {final_result['failed_tests']}")
    print(f"  total_tests: {final_result['total_tests']}")

    print("[PYTHON] COMPLETE")
    print(f"  score: {final_result['score']}")
    print(f"  passed: {final_result['passed']}")
    print(f"  passed_tests: {final_result['passed_tests']}/{final_result['total_tests']}")
    print(f"  failed_tests: {final_result['failed_tests']}")

    return final_result
