"""Markdownlint validator for Solo Dev LLM Bench.

Runs markdownlint against markdown files and reports violations.
Uses the markdownlint CLI if available, falling back to a Python
implementation via the `markdownlint` pip package.
"""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

# Default markdownlint rules (standard set)
DEFAULT_RULES = {
    "MD013": {"line_length": 120},  # Line length
    "MD010": {"code_blocks": True},  # No bare URLs
    "MD026": {"punctuation": ".,;:!+"},  # No trailing punctuation in lists
    "MD033": {"allowed_elements": ["a", "code", "em", "strong"]},  # Inline HTML
}

# ------------------------------------------------------------------
# Markdownlint CLI detection
# ------------------------------------------------------------------

def _find_markdownlint() -> str | None:
    """Try to find markdownlint CLI on the system."""
    # Try markdownlint-cli
    for cmd in ["markdownlint", "markdownlint-cli", "npx markdownlint"]:
        try:
            subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                timeout=5,
            )
            return cmd
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
    return None


def _has_python_markdownlint() -> bool:
    """Check if the python markdownlint package is installed."""
    try:
        import markdownlint  # noqa: F401
        return True
    except ImportError:
        return False


# ------------------------------------------------------------------
# Validator classes
# ------------------------------------------------------------------

class MarkdownLintResult:
    """Result from running markdownlint."""

    def __init__(
        self,
        violations: list[dict],
        output: str,
        command_used: str,
    ):
        self.violations = violations
        self.output = output
        self.command_used = command_used

    @property
    def count(self) -> int:
        return len(self.violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "violations": self.violations,
            "count": self.count,
            "command_used": self.command_used,
            "output": self.output,
        }


class MarkdownLintValidator:
    """Run markdownlint against markdown files."""

    def __init__(
        self,
        rules: dict | None = None,
        config: dict | None = None,
    ):
        self.rules = rules or DEFAULT_RULES
        self.config = config

    def validate_file(self, file_path: Path) -> MarkdownLintResult:
        """Validate a single markdown file."""
        # Try CLI first
        cli_result = self._try_cli(file_path)
        if cli_result:
            return cli_result

        # Fall back to Python
        py_result = self._try_python(file_path)
        if py_result:
            return py_result

        # Neither available
        return MarkdownLintResult(
            violations=[],
            output="",
            command_used="none",
        )

    def validate_string(self, content: str, filename: str = "input.md") -> MarkdownLintResult:
        """Validate markdown content from a string."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()
        ) as f:
            f.write(content)
            f.flush()
            temp_path = Path(f.name)

        try:
            return self.validate_file(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _try_cli(self, file_path: Path) -> MarkdownLintResult | None:
        """Try to use markdownlint CLI."""
        # Try markdownlint CLI
        try:
            cmd = ["markdownlint", "--json", str(file_path)]
            if self.config:
                config_path = self._write_config()
                cmd.extend(["--config", config_path])

            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
                text=True,
            )

            if result.returncode == 0 or result.stdout.strip():
                violations = []
                if result.stdout.strip():
                    try:
                        violations = json.loads(result.stdout)
                    except json.JSONDecodeError:
                        pass

                return MarkdownLintResult(
                    violations=violations,
                    output=result.stdout,
                    command_used="markdownlint-cli",
                )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return None

    def _try_python(self, file_path: Path) -> MarkdownLintResult | None:
        """Try to use Python markdownlint package."""
        if not _has_python_markdownlint():
            return None

        try:
            import markdownlint

            result = markdownlint.lint(str(file_path), rules=self.rules)
            violations = []
            for v in result:
                violations.append({
                    "rule": v.get("rule", ""),
                    "message": v.get("message", ""),
                    "line": v.get("line", 0),
                    "column": v.get("column", 0),
                })

            return MarkdownLintResult(
                violations=violations,
                output=json.dumps(result, indent=2),
                command_used="python-markdownlint",
            )
        except ImportError:
            return None

    def _write_config(self) -> str:
        """Write config to temp file and return path."""
        config_path = Path(tempfile.gettempdir()) / ".markdownlint.json"
        config_path.write_text(json.dumps(self.config or {}))
        return str(config_path)

    def check_dependency(self) -> dict[str, Any]:
        """Check if markdownlint is available."""
        cli_available = _find_markdownlint() is not None
        py_available = _has_python_markdownlint()

        return {
            "cli_available": cli_available,
            "python_available": py_available,
            "message": (
                "markdownlint is available"
                if cli_available or py_available
                else "markdownlint is not available. Install via 'npm install -g markdownlint-cli' or 'pip install markdownlint'."
            ),
        }


# ------------------------------------------------------------------
# Scoring
# ------------------------------------------------------------------

def calculate_score(
    initial_errors: int,
    final_errors: int,
) -> dict[str, Any]:
    """Calculate benchmark score from error counts.

    Args:
        initial_errors: Number of violations in original document.
        final_errors: Number of violations in corrected document.

    Returns:
        Dict with score, passed, and details.
    """
    if initial_errors == 0:
        return {
            "score": 1.0 if final_errors == 0 else 0.0,
            "passed": final_errors == 0,
            "initial_errors": 0,
            "final_errors": final_errors,
            "errors_fixed": 0,
            "message": "Original had no errors",
        }

    # errors_fixed / initial_errors, clamped to [0, 1]
    raw_score = (initial_errors - final_errors) / initial_errors
    score = max(0.0, min(1.0, raw_score))

    return {
        "score": round(score, 4),
        "passed": final_errors == 0,
        "initial_errors": initial_errors,
        "final_errors": final_errors,
        "errors_fixed": max(0, initial_errors - final_errors),
        "message": (
            "PASS" if final_errors == 0
            else f"FAIL: {final_errors} errors remaining"
        ),
    }


# ------------------------------------------------------------------
# High-level API
# ------------------------------------------------------------------

def run_markdownlint_benchmark(
    original_content: str,
    corrected_content: str,
    rules: dict | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    """Run a complete markdownlint benchmark.

    Args:
        original_content: Original broken markdown content.
        corrected_content: Model-corrected markdown content.
        rules: Optional markdownlint rules.
        config: Optional markdownlint config.

    Returns:
        Dict with benchmark results.
    """
    validator = MarkdownLintValidator(rules=rules, config=config)

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
        f.write(corrected_content)
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

    return {
        "initial_errors": initial_errors,
        "final_errors": final_errors,
        "errors_fixed": score_info["errors_fixed"],
        "score": score_info["score"],
        "passed": score_info["passed"],
        "original_violations": original_result.violations,
        "corrected_violations": corrected_result.violations,
        "corrected_output": corrected_output,
        "command_used": original_result.command_used or corrected_result.command_used,
        "dependency_message": validator.check_dependency()["message"],
    }