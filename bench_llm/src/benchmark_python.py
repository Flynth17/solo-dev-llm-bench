"""Python benchmark for Solo Dev LLM Bench.

Evaluates how well the model can produce correct, well-structured
Python code.  Measures:

- **Syntax Validity** — can the output be compiled as valid Python?
- **Type Hinting** — presence of type annotations
- **Docstrings** — presence of docstrings in functions/classes
- **PEP 8 Compliance** — basic style checks (indentation, naming)
- **Token Efficiency** — tokens/sec during code generation
"""

import ast
import re
import time
from typing import Any


# ------------------------------------------------------------------
# Scoring rubric
# ------------------------------------------------------------------

PYTHON_SCORE_WEIGHTS = {
    "syntax_valid": 0.30,
    "type_hints": 0.20,
    "docstrings": 0.15,
    "pep8_basic": 0.15,
    "imports": 0.10,
    "function_calls": 0.10,
}

MIN_SYNTAX_SCORE = 1.0          # must be valid syntax
MIN_STRUCTURE_SCORE = 0.35      # 35 % of max score for other features


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _check_syntax(code: str) -> tuple[bool, str | None]:
    """Return (valid, error_message)."""
    try:
        ast.parse(code)
        return True, None
    except SyntaxError as e:
        return False, str(e)


def _score_type_hints(code: str) -> float:
    """Score presence of type hints in function definitions."""
    func_defs = len(re.findall(r"\bdef\s+\w+\s*\(", code))
    if func_defs == 0:
        return 1.0  # no functions = N/A = full score
    hinted = len(re.findall(r":\s*(?:int|str|float|bool|list|dict|tuple|set|Optional|Any|List|Dict)", code))
    return min(hinted / func_defs, 1.0)


def _score_docstrings(code: str) -> float:
    """Score presence of docstrings."""
    # Count functions/classes that have docstrings
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return 0.0
    docstring_count = 0
    total_defs = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            total_defs += 1
            if (node.body and isinstance(node.body[0], ast.Constant)
                    and isinstance(node.body[0].value, str)):
                docstring_count += 1
    if total_defs == 0:
        return 1.0
    return docstring_count / total_defs


def _score_pep8_basic(code: str) -> float:
    """Basic PEP 8 checks: consistent indentation, line length < 120."""
    if not code.strip():
        return 0.0
    lines = code.splitlines()
    issues = 0
    total = max(len(lines), 1)
    for line in lines:
        # Check for tabs (should be spaces)
        if "\t" in line:
            issues += 1
        # Check for lines > 120 chars
        if len(line) > 120:
            issues += 1
    return max(1.0 - issues / total, 0.0)


def _score_imports(code: str) -> float:
    """Score presence of import statements."""
    import_count = len(re.findall(r"^\s*(?:import|from)\s+", code, re.M))
    return min(import_count / 2.0, 1.0)


def _score_function_calls(code: str) -> float:
    """Score presence of function/method calls."""
    call_count = len(re.findall(r"\w+\s*\(", code))
    return min(call_count / 3.0, 1.0)


_SCORERS = {
    "type_hints": _score_type_hints,
    "docstrings": _score_docstrings,
    "pep8_basic": _score_pep8_basic,
    "imports": _score_imports,
    "function_calls": _score_function_calls,
}


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def evaluate_python(code: str) -> dict[str, Any]:
    """Evaluate Python code quality and return per-feature scores."""
    syntax_valid, syntax_error = _check_syntax(code)

    feature_scores: dict[str, float] = {}
    for feature, scorer in _SCORERS.items():
        feature_scores[feature] = round(scorer(code), 4)

    syntax_score = 1.0 if syntax_valid else 0.0
    weighted = syntax_score * PYTHON_SCORE_WEIGHTS["syntax_valid"]
    weighted += sum(
        feature_scores[f] * PYTHON_SCORE_WEIGHTS[f]
        for f in PYTHON_SCORE_WEIGHTS
    )

    return {
        "syntax_valid": syntax_valid,
        "syntax_error": syntax_error,
        "feature_scores": feature_scores,
        "overall_score": round(weighted, 4),
        "meets_minimum": syntax_valid and weighted >= MIN_STRUCTURE_SCORE,
    }


async def run_python_benchmark(
    lm_studio_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    iterations: int = 3,
) -> dict:
    """Run the Python benchmark against an LM Studio endpoint.

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

        # Extract Python code from markdown code blocks if present
        code_match = re.search(r"```(?:python|py)?\n(.*?)```", generated_text, re.DOTALL)
        code = code_match.group(1) if code_match else generated_text

        py_eval = evaluate_python(code)

        runs.append({
            "iteration": i,
            "cold_or_warm": "cold" if i == 1 else "warm",
            "tokens_per_second": stats.get("tokens_per_second", 0),
            "ttft_seconds": stats.get("time_to_first_token_seconds", 0),
            "input_tokens": stats.get("input_tokens", 0),
            "output_tokens": stats.get("total_output_tokens", 0),
            "model_load_time_seconds": stats.get("model_load_time_seconds", None),
            "wall_time_seconds": round(elapsed, 4),
            # Python-specific fields
            "python_syntax_valid": py_eval["syntax_valid"],
            "python_score": py_eval["overall_score"],
            "python_meets_minimum": py_eval["meets_minimum"],
            "generated_code_length": len(code),
        })

    # Aggregate
    tps_values = [r["tokens_per_second"] for r in runs if r["tokens_per_second"] > 0]
    py_scores = [r["python_score"] for r in runs if r.get("python_score") is not None]
    syntax_pass = [r["python_syntax_valid"] for r in runs]

    aggregate = {}
    if tps_values:
        aggregate["avg_tokens_per_second"] = round(sum(tps_values) / len(tps_values), 2)
        aggregate["min_tokens_per_second"] = round(min(tps_values), 2)
        aggregate["max_tokens_per_second"] = round(max(tps_values), 2)

    if py_scores:
        aggregate["avg_python_score"] = round(sum(py_scores) / len(py_scores), 4)
        aggregate["min_python_score"] = round(min(py_scores), 4)
        aggregate["max_python_score"] = round(max(py_scores), 4)
        aggregate["syntax_pass_rate"] = sum(syntax_pass) / len(syntax_pass)
        aggregate["python_meets_minimum"] = all(py_scores) >= MIN_STRUCTURE_SCORE

    return {
        "benchmark_type": "python",
        "aggregate": aggregate,
        "runs": runs,
    }


# ------------------------------------------------------------------
# Default prompts
# ------------------------------------------------------------------

DEFAULT_PROMPTS = [
    {
        "name": "Data Processing Function",
        "prompt": (
            "Write a Python function called `process_data` that:\n"
            "1. Takes a list[int] and a str as parameters with type hints\n"
            "2. Has a docstring explaining its purpose\n"
            "3. Filters the list to keep only even numbers\n"
            "4. Returns a dict with the original length and filtered list\n"
            "5. Include import statements\n"
            "Keep it under 300 tokens."
        ),
    },
    {
        "name": "Class with Methods",
        "prompt": (
            "Write a Python class called `Counter` that:\n"
            "1. Has a docstring\n"
            "2. Has an __init__ method with type hints\n"
            "3. Has methods: increment, decrement, get_count\n"
            "4. Each method has a docstring\n"
            "5. Uses self.count to track state\n"
            "Keep it under 350 tokens."
        ),
    },
    {
        "name": "Error Handling Example",
        "prompt": (
            "Write a Python function called `safe_divide` that:\n"
            "1. Takes two float parameters and returns a float or None\n"
            "2. Has a docstring\n"
            "3. Handles ZeroDivisionError with try/except\n"
            "4. Returns None when dividing by zero\n"
            "5. Includes type hints and import statements\n"
            "Keep it under 200 tokens."
        ),
    },
]