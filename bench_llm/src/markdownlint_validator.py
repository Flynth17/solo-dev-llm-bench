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
    """Try to find markdownlint CLI on the system.

    Returns one of: 'markdownlint-cli', 'markdownlint-cli2', or None.
    Priority: cli2 > standard cli (cli2 is newer and more widely available via npx).
    """
    # Try markdownlint-cli2 first (newer, more widely available via npx)
    for cmd_prefix in ["npx markdownlint-cli2", "markdownlint-cli2"]:
        try:
            subprocess.run(
                cmd_prefix.split() + ["--version"],
                capture_output=True,
                timeout=5,
            )
            return cmd_prefix
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue

    # Try standard markdownlint CLI
    for cmd in ["markdownlint", "markdownlint-cli"]:
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


def _is_cli2(cmd: str) -> bool:
    """Check if the detected command is markdownlint-cli2."""
    return "markdownlint-cli2" in cmd


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

    # Status constants — must distinguish between completed and unavailable states.
    STATUS_COMPLETED = "completed"
    STATUS_UNAVAILABLE = "unavailable"

    def __init__(
        self,
        violations: list[dict],
        output: str,
        command_used: str,
        status: str = STATUS_COMPLETED,
        error_message: str = "",
    ):
        self.violations = violations
        self.output = output
        self.command_used = command_used
        self.status = status
        self.error_message = error_message

    @property
    def count(self) -> int:
        return len(self.violations)

    @property
    def is_available(self) -> bool:
        """True if validation was actually performed."""
        return self.status == MarkdownLintResult.STATUS_COMPLETED

    def to_dict(self) -> dict[str, Any]:
        d = {
            "violations": self.violations,
            "count": self.count,
            "command_used": self.command_used,
            "output": self.output,
            "status": self.status,
        }
        if self.error_message:
            d["error_message"] = self.error_message
        return d

    @classmethod
    def unavailable(cls, message: str) -> "MarkdownLintResult":
        """Factory for an explicit 'validator unavailable' result.

        This MUST NOT be treated as a zero-error validation.
        """
        return cls(
            violations=[],
            output="",
            command_used="none",
            status=cls.STATUS_UNAVAILABLE,
            error_message=message,
        )


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
        # Try CLI2 first (newer, more widely available via npx)
        cli2_result = self._try_cli2(file_path)
        if cli2_result:
            return cli2_result

        # Try standard CLI as fallback
        cli_result = self._try_cli(file_path)
        if cli_result:
            return cli_result

        # Fall back to Python
        py_result = self._try_python(file_path)
        if py_result:
            return py_result

        # Neither available — MUST NOT be treated as a zero-error validation.
        dep = self.check_dependency()
        msg = (
            "markdownlint is not available: neither CLI nor python-markdownlint package found. "
            "Install via 'npm install -g markdownlint-cli' or 'pip install markdownlint'."
        )
        return MarkdownLintResult.unavailable(msg)

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

    def _try_cli2(self, file_path: Path) -> MarkdownLintResult | None:
        """Try to use markdownlint-cli2.

        cli2 outputs human-readable text like:
          path.md:13 error MD040/fenced-code-language Fenced code blocks ...
          path.md:69:13 error MD034/no-bare-urls Bare URL used ...

        Parses this into the same MarkdownLintResult structure.
        """
        try:
            cmd = ["npx", "markdownlint-cli2", "--no-globs", str(file_path)]
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
                text=True,
            )

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            combined = ""
            if stdout:
                combined += stdout
            if stderr:
                if combined:
                    combined += "\n"
                combined += stderr

            # Parse human-readable output: "path.md:line:error RULE_ID message"
            violations = []
            for line in combined.splitlines():
                line = line.strip()
                if not line or line.startswith("markdownlint-cli2") or line.startswith("Finding:") or line.startswith("Linting:") or line.startswith("Summary:") or line.startswith("$"):
                    continue
                # Format: path.md:line:error RULE_ID/message message text
                match = self._parse_cli2_line(line)
                if match:
                    violations.append(match)

            if violations or combined.strip():
                return MarkdownLintResult(
                    violations=violations,
                    output=combined,
                    command_used="markdownlint-cli2",
                )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return None

    @staticmethod
    def _parse_cli2_line(line: str) -> dict | None:
        """Parse a single markdownlint-cli2 output line.

        Expected formats (cli2 human-readable):
          path.md:line error RULE_ID/message message text
          path.md:line:col error RULE_ID/message message text

        The key insight is that the level keyword (error/warn/info) always appears
        before the rule ID, so we match from there backwards.
        """
        import re
        # Match from the end: ... error|warn|info RULE_ID/rule_msg detail
        pattern = r'^(.+?):(\d+)(?::(\d+))?\s+(error|warn|info)\s+(\S+)/(\S+)\s+(.+)$'
        m = re.search(pattern, line)
        if not m:
            return None
        filepath, lineno, col, level, rule_name, rule_msg, detail = m.groups()
        result = {
            "file": filepath.strip(),
            "line": int(lineno),
            "level": level,
            "rule": rule_name.strip(),
            "message": f"{rule_msg.strip()}: {detail.strip()}",
        }
        if col:
            result["column"] = int(col)
        return result

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
    finally:
        original_path.unlink(missing_ok=True)

    # If the validator is unavailable, short-circuit immediately.
    if not original_result.is_available:
        return {
            "initial_errors": None,
            "final_errors": None,
            "errors_fixed": None,
            "score": None,
            "passed": False,
            "original_violations": [],
            "corrected_violations": [],
            "corrected_output": "",
            "command_used": "none",
            "dependency_message": validator.check_dependency()["message"],
            "validator_available": False,
            "error": original_result.error_message,
        }

    initial_errors = original_result.count

    # Validate corrected
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()
    ) as f:
        f.write(corrected_content)
        f.flush()
        corrected_path = Path(f.name)

    try:
        corrected_result = validator.validate_file(corrected_path)
    finally:
        corrected_path.unlink(missing_ok=True)

    # If the corrected validation also failed (shouldn't happen if original passed), propagate.
    if not corrected_result.is_available:
        return {
            "initial_errors": initial_errors,
            "final_errors": None,
            "errors_fixed": None,
            "score": None,
            "passed": False,
            "original_violations": original_result.violations,
            "corrected_violations": [],
            "corrected_output": "",
            "command_used": original_result.command_used,
            "dependency_message": validator.check_dependency()["message"],
            "validator_available": False,
            "error": corrected_result.error_message,
        }

    final_errors = corrected_result.count
    corrected_output = corrected_result.output

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
