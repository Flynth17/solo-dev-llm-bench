"""Tests for the Java correctness LLM task runner (src/task_java.py).

Tests cover:
1. prompt.md loads
2. Solution.java loads
3. TestSolution.java content is NOT in model prompt
4. string LM Studio output works
5. list output works
6. dict output works
7. Markdown fences are stripped
8. correct Java -> 7/7, score 1.0
9. partial Java -> partial score
10. compile-broken Java -> 0 score
11. validator result passes through unchanged
"""

import sys
import os
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.task_java import (
    build_java_prompt,
    normalize_llm_output,
    _strip_fences,
    JavaCorrectnessResult,
    run_java_correctness_task,
    _FIXTURE_DIR,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def _correct_code():
    """Return the correct Solution.java code."""
    return '''/**
 * A simple calculator utility class.
 */
public class Solution {

    /**
     * Add two integers and return the result.
     */
    public static int add(int a, int b) {
        return a + b;
    }

    /**
     * Multiply two integers and return the result.
     */
    public static int multiply(int x, int y) {
        return x * y;
    }

    /**
     * Return true if n is even, false otherwise.
     */
    public static boolean isEven(int n) {
        return n % 2 == 0;
    }

    /**
     * Return the absolute value of n.
     */
    public static int absolute(int n) {
        if (n < 0) {
            return -n;
        }
        return n;
    }

    /**
     * Return the greeting for a given name.
     */
    public static String greeting(String name) {
        return "Hello " + name + "!";
    }
}
'''


def _partial_code():
    """Return a partial solution (only add() fixed)."""
    return '''public class Solution {
    public static int add(int a, int b) {
        return a + b;
    }
    public static int multiply(int x, int y) { return x + y; }
    public static boolean isEven(int n) { return n % 2 == 1; }
    public static int absolute(int n) { if (n > 0) return -n; return n; }
    public static String greeting(String name) { return "Hello" + name + "!"; }
}
'''


def _broken_code():
    """Return compile-broken Java code."""
    return "public class Solution { public int add(int a, int b) { return a + b; }"


# ------------------------------------------------------------------
# Test 1: prompt.md loads
# ------------------------------------------------------------------

class TestPromptLoads:
    """Test that prompt.md loads correctly."""

    def test_prompt_md_loads(self):
        """prompt.md must load and be non-empty."""
        prompt_path = _FIXTURE_DIR / "prompt.md"
        assert prompt_path.exists()
        content = prompt_path.read_text(encoding="utf-8")
        assert len(content) > 0
        assert "Fix" in content or "fix" in content


# ------------------------------------------------------------------
# Test 2: Solution.java loads
# ------------------------------------------------------------------

class TestSolutionLoads:
    """Test that Solution.java fixture loads."""

    def test_solution_java_loads(self):
        """Solution.java must exist and contain class definition."""
        sol_path = _FIXTURE_DIR / "Solution.java"
        assert sol_path.exists()
        content = sol_path.read_text(encoding="utf-8")
        assert "class Solution" in content


# ------------------------------------------------------------------
# Test 3: TestSolution.java is NOT in model prompt
# ------------------------------------------------------------------

class TestHiddenTests:
    """Test that TestSolution.java is hidden from the model prompt."""

    def test_test_fixture_not_in_prompt(self):
        """TestSolution.java content must NOT appear in the model prompt."""
        test_solution_path = _FIXTURE_DIR / "TestSolution.java"
        test_content = test_solution_path.read_text(encoding="utf-8")

        prompt = build_java_prompt()

        # TestSolution.java should not leak into the prompt
        assert "TestSolution" not in prompt
        assert "check(" not in prompt
        assert "testAdd" not in prompt


# ------------------------------------------------------------------
# Test 4-6: LM Studio output normalization
# ------------------------------------------------------------------

class TestStringOutput:
    """Test string output normalization."""

    def test_string_output_works(self):
        """Plain string output must be returned trimmed."""
        code = "public class Solution { }"
        result = normalize_llm_output(code)
        assert result == code


class TestListOutput:
    """Test list output normalization."""

    def test_list_output_works(self):
        """List output must be joined."""
        parts = ["public class Solution {", "}", ""]
        result = normalize_llm_output(parts)
        assert "public class Solution" in result
        assert "}" in result


class TestDictOutput:
    """Test dict output normalization."""

    def test_dict_output_with_output_key(self):
        """Dict with 'output' key must extract it."""
        d = {"output": "public class Solution { }"}
        result = normalize_llm_output(d)
        assert result == "public class Solution { }"

    def test_dict_output_with_text_key(self):
        """Dict with 'text' key must extract it."""
        d = {"text": "public class Solution { }"}
        result = normalize_llm_output(d)
        assert result == "public class Solution { }"

    def test_dict_nested_text(self):
        """Dict with nested text must extract inner text."""
        d = {"output": {"text": "public class Solution { }"}}
        result = normalize_llm_output(d)
        assert result == "public class Solution { }"


# ------------------------------------------------------------------
# Test 7: Markdown fences are stripped
# ------------------------------------------------------------------

class TestFenceStripping:
    """Test Markdown code fence stripping."""

    def test_java_fence_stripped(self):
        """```java ... ``` must be stripped."""
        code = "```java\npublic class Solution { }\n```"
        result = _strip_fences(code)
        assert result == "public class Solution { }"

    def test_generic_fence_stripped(self):
        """``` ... ``` must be stripped."""
        code = "```\npublic class Solution { }\n```"
        result = _strip_fences(code)
        assert result == "public class Solution { }"

    def test_no_fence_unchanged(self):
        """Code without fences must be unchanged."""
        code = "public class Solution { }"
        result = _strip_fences(code)
        assert result == code


# ------------------------------------------------------------------
# Test 8: correct Java -> 7/7, score 1.0
# ------------------------------------------------------------------

class TestCorrectJava:
    """Test that correct Java produces score 1.0."""

    def test_correct_java_scores_1(self):
        """Correct Solution.java must score 1.0."""
        from src.java_validator import validate_java_solution
        result = validate_java_solution(_correct_code())
        assert result.score == 1.0
        assert result.passed is True
        assert result.passed_tests == 7
        assert result.total_tests == 7


# ------------------------------------------------------------------
# Test 9: partial Java -> partial score
# ------------------------------------------------------------------

class TestPartialJava:
    """Test that partial Java produces partial score."""

    def test_partial_java_scores_less_than_1(self):
        """Partial Solution.java must score < 1.0."""
        from src.java_validator import validate_java_solution
        result = validate_java_solution(_partial_code())
        assert result.score < 1.0
        assert result.score >= 0.0


# ------------------------------------------------------------------
# Test 10: compile-broken Java -> 0 score
# ------------------------------------------------------------------

class TestBrokenJava:
    """Test that compile-broken Java produces score 0."""

    def test_broken_java_scores_0(self):
        """Broken Solution.java must score 0.0."""
        from src.java_validator import validate_java_solution
        result = validate_java_solution(_broken_code())
        assert result.score == 0.0
        assert result.passed is False
        assert result.compile_success is False


# ------------------------------------------------------------------
# Test 11: validator result passes through unchanged
# ------------------------------------------------------------------

class TestValidatorPassthrough:
    """Test that validator result is preserved in JavaCorrectnessResult."""

    def test_validator_result_preserved(self):
        """Validator result must be accessible from JavaCorrectnessResult."""
        from src.java_validator import validate_java_solution
        vr = validate_java_solution(_correct_code())

        # Create result with mocked values
        result = JavaCorrectnessResult(
            task_name="java_correctness",
            task_type="java_correctness",
            model="test-model",
            score=vr.score,
            passed=vr.passed,
            total_tests=vr.total_tests,
            passed_tests=vr.passed_tests,
            failed_tests=vr.failed_tests,
            compile_success=vr.compile_success,
            output_tokens=100,
            input_tokens=50,
            tokens_per_second=10.0,
            ttft_seconds=0.5,
            wall_time_seconds=5.0,
            generated_code=_correct_code(),
            timestamp="2025-01-01T00:00:00+00:00",
            hardware_label="local",
            connection_type="local",
            validator_result=vr,
        )

        assert result.score == 1.0
        assert result.passed is True
        assert result.total_tests == 7
        assert result.validator_result is not None
        assert result.validator_result.score == 1.0

    def test_to_dict_serialization(self):
        """to_dict must include all required fields."""
        result = JavaCorrectnessResult(
            task_name="java_correctness",
            task_type="java_correctness",
            model="test-model",
            score=1.0,
            passed=True,
            total_tests=7,
            passed_tests=7,
            failed_tests=0,
            compile_success=True,
            output_tokens=100,
            input_tokens=50,
            tokens_per_second=10.0,
            ttft_seconds=0.5,
            wall_time_seconds=5.0,
            generated_code=_correct_code(),
            timestamp="2025-01-01T00:00:00+00:00",
            hardware_label="local",
            connection_type="local",
            validator_result=None,
        )

        d = result.to_dict()

        # All required fields
        assert d["task_name"] == "java_correctness"
        assert d["task_type"] == "java_correctness"
        assert d["model"] == "test-model"
        assert d["score"] == 1.0
        assert d["passed"] is True
        assert d["total_tests"] == 7
        assert d["passed_tests"] == 7
        assert d["failed_tests"] == 0
        assert d["compile_success"] is True
        assert d["output_tokens"] == 100
        assert d["input_tokens"] == 50
        assert d["tokens_per_second"] == 10.0
        assert d["ttft_seconds"] == 0.5
        assert d["wall_time_seconds"] == 5.0
        assert d["generated_code"] == _correct_code()
        assert d["timestamp"] == "2025-01-01T00:00:00+00:00"
        assert d["hardware_label"] == "local"
        assert d["connection_type"] == "local"


# ------------------------------------------------------------------
# Test 12: build_java_prompt integration
# ------------------------------------------------------------------

class TestBuildPrompt:
    """Test build_java_prompt function."""

    def test_prompt_contains_broken_code(self):
        """build_java_prompt must include the broken Solution.java."""
        prompt = build_java_prompt()
        assert "class Solution" in prompt

    def test_prompt_contains_instructions(self):
        """build_java_prompt must include instructions."""
        prompt = build_java_prompt()
        assert "Fix" in prompt or "fix" in prompt

    def test_prompt_does_not_contain_test_harness(self):
        """build_java_prompt must NOT include TestSolution.java."""
        prompt = build_java_prompt()
        assert "TestSolution" not in prompt


# ------------------------------------------------------------------
# Test 13: run_java_correctness_task with mocked HTTP
# ------------------------------------------------------------------

class TestRunJavaCorrectnessTask:
    """Test the async run_java_correctness_task function."""

    @pytest.mark.asyncio
    async def test_run_java_correctness_with_mocked_http(self):
        """run_java_correctness_task must produce correct result with mocked HTTP."""
        # Mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": "```java\n" + _correct_code() + "\n```",
            "stats": {
                "input_tokens": 500,
                "total_output_tokens": 200,
                "tokens_per_second": 50.0,
                "time_to_first_token_seconds": 0.3,
            },
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await run_java_correctness_task(
                lm_studio_url="http://localhost:1234",
                model="test-model",
                temperature=0.0,
                max_output_tokens=1024,
                hardware_label="local",
                connection_type="local",
            )

        assert result.task_name == "java_correctness"
        assert result.task_type == "java_correctness"
        assert result.model == "test-model"
        assert result.score == 1.0
        assert result.passed is True
        assert result.total_tests == 7
        assert result.passed_tests == 7
        assert result.failed_tests == 0
        assert result.compile_success is True
        assert result.input_tokens == 500
        assert result.output_tokens == 200
        assert result.tokens_per_second == 50.0
        assert result.ttft_seconds == 0.3
        assert result.wall_time_seconds > 0
        assert "test-model" in result.generated_code or "Solution" in result.generated_code
        assert result.hardware_label == "local"
        assert result.connection_type == "local"

    @pytest.mark.asyncio
    async def test_run_java_correctness_partial_score(self):
        """run_java_correctness_task must produce partial score for partial code."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": _partial_code(),
            "stats": {
                "input_tokens": 500,
                "total_output_tokens": 100,
                "tokens_per_second": 40.0,
                "time_to_first_token_seconds": 0.2,
            },
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await run_java_correctness_task(
                lm_studio_url="http://localhost:1234",
                model="test-model",
                temperature=0.0,
                max_output_tokens=1024,
                hardware_label="local",
                connection_type="local",
            )

        assert result.score < 1.0
        assert result.passed is False
        assert result.total_tests == 7

    @pytest.mark.asyncio
    async def test_run_java_correctness_broken_code(self):
        """run_java_correctness_task must produce 0 score for broken code."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": _broken_code(),
            "stats": {
                "input_tokens": 500,
                "total_output_tokens": 50,
                "tokens_per_second": 30.0,
                "time_to_first_token_seconds": 0.1,
            },
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await run_java_correctness_task(
                lm_studio_url="http://localhost:1234",
                model="test-model",
                temperature=0.0,
                max_output_tokens=1024,
                hardware_label="local",
                connection_type="local",
            )

        assert result.score == 0.0
        assert result.passed is False
        assert result.compile_success is False

    @pytest.mark.asyncio
    async def test_run_java_correctness_dict_output(self):
        """run_java_correctness_task must handle dict output from LM Studio."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": {"text": _correct_code()},
            "stats": {
                "input_tokens": 500,
                "total_output_tokens": 200,
                "tokens_per_second": 50.0,
                "time_to_first_token_seconds": 0.3,
            },
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await run_java_correctness_task(
                lm_studio_url="http://localhost:1234",
                model="test-model",
                temperature=0.0,
                max_output_tokens=1024,
                hardware_label="local",
                connection_type="local",
            )

        assert result.score == 1.0
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_run_java_correctness_list_output(self):
        """run_java_correctness_task must handle list output from LM Studio."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": ["public class Solution {", _correct_code(), "}"],
            "stats": {
                "input_tokens": 500,
                "total_output_tokens": 200,
                "tokens_per_second": 50.0,
                "time_to_first_token_seconds": 0.3,
            },
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await run_java_correctness_task(
                lm_studio_url="http://localhost:1234",
                model="test-model",
                temperature=0.0,
                max_output_tokens=1024,
                hardware_label="local",
                connection_type="local",
            )

        # List output will be joined, so the code won't be valid Java
        # but it should not crash
        assert result.task_name == "java_correctness"


# ------------------------------------------------------------------
# Test 14: LM Studio reasoning/message block extraction (regression A-G)
# ------------------------------------------------------------------

def _reasoning_blocks_response():
    """Realistic LM Studio chat response: reasoning block(s) + final message."""
    return [
        {"type": "reasoning", "content": "thinking about the bugs..."},
        {"type": "message", "content": _correct_code()},
    ]


class TestReasoningMessageExtraction:
    """Regression tests for LM Studio reasoning-capable response shapes.

    body["output"] may be a list of blocks:
      [{"type": "reasoning", "content": "<thinking>"},
       {"type": "message",   "content": "<final Java source>"}]
    normalize_llm_output() must return ONLY the final message content and
    never leak Python dict repr text into generated_code.
    """

    # A) reasoning + message -> only the Java message content
    def test_reasoning_plus_message_returns_only_java(self):
        result = normalize_llm_output(_reasoning_blocks_response())
        assert "thinking about the bugs" not in result
        assert "public class Solution" in result
        assert result == _correct_code().strip()

    # B) multiple reasoning blocks followed by message -> last message only
    def test_multiple_reasoning_then_message(self):
        response = [
            {"type": "reasoning", "content": "first thinking chunk"},
            {"type": "reasoning", "content": "second thinking chunk"},
            {"type": "message", "content": _correct_code()},
        ]
        result = normalize_llm_output(response)
        assert "first thinking chunk" not in result
        assert "second thinking chunk" not in result
        assert result == _correct_code().strip()

    # C) reasoning only, no message -> no dict repr leakage
    def test_reasoning_only_no_dict_repr(self):
        response = [
            {"type": "reasoning", "content": "I will now produce the code."},
        ]
        result = normalize_llm_output(response)
        assert "{'type':" not in result
        assert "'type': 'reasoning'" not in result
        # Safe fallback extracts textual content only.
        assert "produce the code" in result

    def test_reasoning_only_empty_content_no_dict_repr(self):
        response = [{"type": "reasoning", "content": ""}]
        result = normalize_llm_output(response)
        assert "{'type':" not in result
        assert result == ""

    # D) existing string output still works
    def test_string_still_works(self):
        code = _correct_code()
        assert normalize_llm_output("  " + code.strip() + "\n") == code.strip()

    # E) existing dict output still works
    def test_dict_still_works(self):
        d = {"output": "public class Solution { }"}
        assert normalize_llm_output(d) == "public class Solution { }"

    # F) existing list-of-strings behavior still works
    def test_list_of_strings_still_works(self):
        parts = ["public class Solution {", "}"]
        result = normalize_llm_output(parts)
        assert result == "public class Solution {\n}"

    # G) realistic reasoning+message response validates as 7/7, score 1.0
    def test_reasoning_plus_message_validates_7_of_7(self):
        from src.java_validator import validate_java_solution

        normalized = normalize_llm_output(_reasoning_blocks_response())
        generated_code = _strip_fences(normalized)
        assert "thinking" not in generated_code.lower()
        assert "{'type':" not in generated_code

        result = validate_java_solution(generated_code)
        assert result.compile_success is True
        assert result.passed_tests == 7
        assert result.total_tests == 7
        assert result.score == 1.0
        assert result.passed is True


class TestRunJavaReasoningMessageE2E:
    """End-to-end run_java_correctness_task with LM Studio reasoning shape."""

    @pytest.mark.asyncio
    async def test_run_with_reasoning_message_blocks_scores_7(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": _reasoning_blocks_response(),
            "stats": {
                "input_tokens": 389,
                "total_output_tokens": 594,
                "tokens_per_second": 62.0,
                "time_to_first_token_seconds": 0.2,
            },
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await run_java_correctness_task(
                lm_studio_url="http://localhost:1234",
                model="gemma-4-31b-qat",
                temperature=0.0,
                max_output_tokens=1024,
                hardware_label="local",
                connection_type="local",
            )

        assert result.passed_tests == 7
        assert result.total_tests == 7
        assert result.score == 1.0
        assert result.compile_success is True
        # generated_code must contain ONLY the final Java answer, never
        # reasoning text or dict repr artifacts.
        assert "public class Solution" in result.generated_code
        assert "thinking about the bugs" not in result.generated_code
        assert "{'type':" not in result.generated_code

    @pytest.mark.asyncio
    async def test_run_with_reasoning_only_no_crash(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": [{"type": "reasoning", "content": "still thinking..."}],
            "stats": {
                "input_tokens": 389,
                "total_output_tokens": 594,
                "tokens_per_second": 62.0,
                "time_to_first_token_seconds": 0.2,
            },
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await run_java_correctness_task(
                lm_studio_url="http://localhost:1234",
                model="glm-4.7-flash",
                temperature=0.0,
                max_output_tokens=1024,
                hardware_label="local",
                connection_type="local",
            )

        # No dict repr contamination; compile fails cleanly (score 0).
        assert "{'type':" not in result.generated_code
        assert result.compile_success is False
        assert result.score == 0.0
