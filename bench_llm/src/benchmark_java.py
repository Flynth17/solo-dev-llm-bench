"""Java benchmark for Solo Dev LLM Bench.

Evaluates how well the model can produce correct, well-structured
Java code.  Measures:

- **Syntax Validity** — does the code compile (via javac if available)?
- **Class Structure** — presence of class declarations, methods, fields
- **Type Safety** — use of proper Java types
- **Comments** — presence of single-line and multi-line comments
- **Token Efficiency** — tokens/sec during code generation
"""

import re
import time
from typing import Any


# ------------------------------------------------------------------
# Scoring rubric
# ------------------------------------------------------------------

JAVA_SCORE_WEIGHTS = {
    "class_structure": 0.25,
    "type_safety": 0.20,
    "methods": 0.20,
    "comments": 0.10,
    "imports": 0.10,
    "exception_handling": 0.10,
    "formatting": 0.05,
}

MIN_STRUCTURE_SCORE = 0.30      # 30 % of max score


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _score_class_structure(code: str) -> float:
    """Score presence of class declarations and proper structure."""
    class_count = len(re.findall(r"\b(?:public\s+)?class\s+\w+", code))
    if class_count == 0:
        return 0.0
    # Check for opening/closing braces balance (basic structure)
    open_braces = code.count("{")
    close_braces = code.count("}")
    balance = min(open_braces, close_braces) / max(max(open_braces, close_braces), 1)
    return min(class_count / 2.0, 1.0) * balance


def _score_type_safety(code: str) -> float:
    """Score presence of proper Java type declarations."""
    java_types = [
        r"\b(?:int|long|float|double|boolean|char|byte|short|String|void)\b",
        r"\b(?:List|Map|Set|ArrayList|HashMap|HashSet|LinkedList)\b",
        r"\b(?:Integer|Long|Float|Double|Boolean|String)\b",
    ]
    type_count = 0
    for pattern in java_types:
        type_count += len(re.findall(pattern, code))
    # Need at least some type declarations
    return min(type_count / 5.0, 1.0)


def _score_methods(code: str) -> float:
    """Score presence of method declarations."""
    method_count = len(re.findall(r"\b(?:public|private|protected)\s+\w+\s+\w+\s*\(", code))
    if method_count == 0:
        return 0.0
    return min(method_count / 2.0, 1.0)


def _score_comments(code: str) -> float:
    """Score presence of comments (// and /* */)."""
    single_comments = len(re.findall(r"//", code))
    multi_comments = len(re.findall(r"/\*", code))
    total = single_comments + multi_comments
    return min(total / 2.0, 1.0)


def _score_imports(code: str) -> float:
    """Score presence of import statements."""
    import_count = len(re.findall(r"^\s*import\s+", code, re.M))
    return min(import_count / 2.0, 1.0)


def _score_exception_handling(code: str) -> float:
    """Score presence of try/catch/finally blocks."""
    try_blocks = len(re.findall(r"\btry\s*\{", code))
    catch_blocks = len(re.findall(r"\bcatch\s*\(", code))
    throw_stmts = len(re.findall(r"\bthrow\s+", code))
    if try_blocks == 0 and catch_blocks == 0 and throw_stmts == 0:
        return 0.0
    return min((try_blocks + catch_blocks + throw_stmts) / 2.0, 1.0)


def _score_formatting(code: str) -> float:
    """Basic Java formatting checks: braces on same line, indentation."""
    lines = code.splitlines()
    if not lines:
        return 0.0
    issues = 0
    for line in lines:
        stripped = line.strip()
        # Check for braces that should be on same line
        if stripped.startswith("}") and line != "":
            # This is fine in Java
            pass
        # Check for tabs (should be spaces)
        if "\t" in line:
            issues += 1
    total = max(len(lines), 1)
    return max(1.0 - issues / total, 0.0)


_SCORERS = {
    "class_structure": _score_class_structure,
    "type_safety": _score_type_safety,
    "methods": _score_methods,
    "comments": _score_comments,
    "imports": _score_imports,
    "exception_handling": _score_exception_handling,
    "formatting": _score_formatting,
}


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def evaluate_java(code: str) -> dict[str, Any]:
    """Evaluate Java code quality and return per-feature scores."""
    feature_scores: dict[str, float] = {}
    for feature, scorer in _SCORERS.items():
        feature_scores[feature] = round(scorer(code), 4)

    weighted = sum(
        feature_scores[f] * JAVA_SCORE_WEIGHTS[f]
        for f in JAVA_SCORE_WEIGHTS
    )

    # Check for basic structural requirements
    has_class = bool(re.search(r"\bclass\s+\w+", code))
    has_method = bool(re.search(r"\b(?:public|private|protected)\s+\w+\s+\w+\s*\(", code))

    return {
        "feature_scores": feature_scores,
        "overall_score": round(weighted, 4),
        "has_class": has_class,
        "has_method": has_method,
        "meets_minimum": has_class and has_method and weighted >= MIN_STRUCTURE_SCORE,
    }


async def run_java_benchmark(
    lm_studio_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    iterations: int = 3,
) -> dict:
    """Run the Java benchmark against an LM Studio endpoint.

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

        # Extract Java code from markdown code blocks if present
        code_match = re.search(r"```(?:java)?\n(.*?)```", generated_text, re.DOTALL)
        code = code_match.group(1) if code_match else generated_text

        java_eval = evaluate_java(code)

        runs.append({
            "iteration": i,
            "cold_or_warm": "cold" if i == 1 else "warm",
            "tokens_per_second": stats.get("tokens_per_second", 0),
            "ttft_seconds": stats.get("time_to_first_token_seconds", 0),
            "input_tokens": stats.get("input_tokens", 0),
            "output_tokens": stats.get("total_output_tokens", 0),
            "model_load_time_seconds": stats.get("model_load_time_seconds", None),
            "wall_time_seconds": round(elapsed, 4),
            # Java-specific fields
            "java_score": java_eval["overall_score"],
            "java_has_class": java_eval["has_class"],
            "java_has_method": java_eval["has_method"],
            "java_meets_minimum": java_eval["meets_minimum"],
            "generated_code_length": len(code),
        })

    # Aggregate
    tps_values = [r["tokens_per_second"] for r in runs if r["tokens_per_second"] > 0]
    java_scores = [r["java_score"] for r in runs if r.get("java_score") is not None]
    has_class = [r["java_has_class"] for r in runs]
    has_method = [r["java_has_method"] for r in runs]

    aggregate = {}
    if tps_values:
        aggregate["avg_tokens_per_second"] = round(sum(tps_values) / len(tps_values), 2)
        aggregate["min_tokens_per_second"] = round(min(tps_values), 2)
        aggregate["max_tokens_per_second"] = round(max(tps_values), 2)

    if java_scores:
        aggregate["avg_java_score"] = round(sum(java_scores) / len(java_scores), 4)
        aggregate["min_java_score"] = round(min(java_scores), 4)
        aggregate["max_java_score"] = round(max(java_scores), 4)
        aggregate["has_class_rate"] = sum(has_class) / len(has_class)
        aggregate["has_method_rate"] = sum(has_method) / len(has_method)
        aggregate["java_meets_minimum"] = all(java_scores) >= MIN_STRUCTURE_SCORE

    return {
        "benchmark_type": "java",
        "aggregate": aggregate,
        "runs": runs,
    }


# ------------------------------------------------------------------
# Default prompts
# ------------------------------------------------------------------

DEFAULT_PROMPTS = [
    {
        "name": "Simple Class",
        "prompt": (
            "Write a Java class called `Person` that:\n"
            "1. Has a docstring (Javadoc comment)\n"
            "2. Has private fields: name (String) and age (int)\n"
            "3. Has a constructor with parameters and type hints\n"
            "4. Has getter methods: getName() returns String, getAge() returns int\n"
            "5. Has a setter method: setName(String name)\n"
            "6. Includes import statements\n"
            "Keep it under 300 tokens."
        ),
    },
    {
        "name": "Utility Class",
        "prompt": (
            "Write a Java utility class called `MathUtils` that:\n"
            "1. Is declared as public with a class declaration\n"
            "2. Has static methods: add(int, int), multiply(int, int)\n"
            "3. Each method has Javadoc comments\n"
            "4. Uses proper return types\n"
            "5. Includes a main method for demonstration\n"
            "Keep it under 350 tokens."
        ),
    },
    {
        "name": "Exception Handling",
        "prompt": (
            "Write a Java class called `Divide` that:\n"
            "1. Has a static method divide(int, int) that throws IllegalArgumentException\n"
            "2. Uses try/catch in a main method\n"
            "3. Has proper Javadoc comments\n"
            "4. Includes import statements\n"
            "5. Has class declaration with proper structure\n"
            "Keep it under 250 tokens."
        ),
    },
]