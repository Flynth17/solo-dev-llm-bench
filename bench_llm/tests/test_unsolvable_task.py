"""Tests for src/task_unsolvable.py — Unsolvable correctness task runner.

Covers:
1. prompt.md loads
2. scenario.md loads
3. final prompt contains scenario
4. prompt construction is deterministic
5. plain string output works
6. dict output works
7. list/message-block output works
8. reasoning blocks are discarded
9. final message is extracted
10. correct structured recognition -> score 1.0
11. incorrect recognition -> score 0.0
12. code-only answer -> score 0.0
13. validator result passes through unchanged
14. performance metadata is returned
15. reasoning+message response returns only final message
"""

import asyncio
from unittest import TestCase, main


class TestPromptLoads(TestCase):
    """Tests 1-2: prompt.md and scenario.md load."""

    def test_prompt_md_loads(self):
        from src.task_unsolvable import _load_fixture
        content = _load_fixture("prompt.md")
        self.assertIsInstance(content, str)
        self.assertTrue(len(content) > 0)

    def test_scenario_md_loads(self):
        from src.task_unsolvable import _load_fixture
        content = _load_fixture("scenario.md")
        self.assertIsInstance(content, str)
        self.assertTrue(len(content) > 0)


class TestPromptConstruction(TestCase):
    """Tests 3-4: final prompt contains scenario and is deterministic."""

    def test_final_prompt_contains_scenario(self):
        from src.task_unsolvable import build_unsolvable_prompt
        prompt = build_unsolvable_prompt()
        self.assertIn("classify", prompt)
        self.assertIn("R1", prompt)
        self.assertIn("R2", prompt)

    def test_prompt_construction_is_deterministic(self):
        from src.task_unsolvable import build_unsolvable_prompt
        p1 = build_unsolvable_prompt()
        p2 = build_unsolvable_prompt()
        self.assertEqual(p1, p2)


class TestOutputNormalization(TestCase):
    """Tests 5-9: output normalization and reasoning/message extraction."""

    def _normalize(self):
        from src.task_unsolvable import normalize_llm_output
        return normalize_llm_output

    def test_plain_string_output(self):
        n = self._normalize()
        result = n("IMPOSSIBLE: yes\nCLASS: contradictory\nCONFLICT: R1, R2\nEXPLANATION: The requirements are logically inconsistent.")
        self.assertEqual(result.strip(), "IMPOSSIBLE: yes\nCLASS: contradictory\nCONFLICT: R1, R2\nEXPLANATION: The requirements are logically inconsistent.")

    def test_dict_output(self):
        n = self._normalize()
        result = n({"output": "test content"})
        self.assertEqual(result, "test content")

    def test_list_message_block_output(self):
        n = self._normalize()
        blocks = [
            {"type": "reasoning", "content": "thinking..."},
            {"type": "message", "content": "IMPOSSIBLE: yes\nCLASS: contradictory\nCONFLICT: R1, R2\nEXPLANATION: The requirements are logically inconsistent."},
        ]
        result = n(blocks)
        self.assertIn("IMPOSSIBLE: yes", result)

    def test_reasoning_blocks_discarded(self):
        n = self._normalize()
        blocks = [
            {"type": "reasoning", "content": "I think this is possible..."},
            {"type": "message", "content": "IMPOSSIBLE: yes\nCLASS: contradictory\nCONFLICT: R1, R2\nEXPLANATION: The requirements are logically inconsistent."},
        ]
        result = n(blocks)
        self.assertNotIn("I think this is possible", result)

    def test_final_message_extracted(self):
        n = self._normalize()
        blocks = [
            {"type": "reasoning", "content": "thinking 1"},
            {"type": "reasoning", "content": "thinking 2"},
            {"type": "message", "content": "FINAL ANSWER"},
        ]
        result = n(blocks)
        self.assertEqual(result, "FINAL ANSWER")


class TestValidatorIntegration(TestCase):
    """Tests 10-13: validator integration with correct/incorrect responses."""

    def _validate(self):
        from src.unsolvable_validator import validate_unsolvable_response
        return validate_unsolvable_response

    def test_correct_structured_recognition_scores_1(self):
        v = self._validate()
        response = (
            "IMPOSSIBLE: yes\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1, R2\n"
            "EXPLANATION: Requirements R1 and R2 are mutually exclusive because they demand that classify(0) return two different values simultaneously, which is logically impossible."
        )
        result = v(response)
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 1.0)
        self.assertTrue(result.impossible_detected)

    def test_incorrect_recognition_scores_0(self):
        v = self._validate()
        response = "IMPOSSIBLE: no\nCLASS: solvable\nCONFLICT: R1\nEXPLANATION: This is a simple classification task that can be solved."
        result = v(response)
        self.assertFalse(result.passed)
        self.assertEqual(result.score, 0.0)

    def test_code_only_answer_scores_0(self):
        v = self._validate()
        response = "def classify(x):\n    return 'zero'"
        result = v(response)
        self.assertFalse(result.passed)
        self.assertEqual(result.score, 0.0)

    def test_validator_result_passes_through(self):
        from src.task_unsolvable import UnsolvableResultData
        # Verify the dataclass has all required fields
        result = UnsolvableResultData(
            task_name="Unsolvable Recognition",
            task_type="unsolvable",
            model="test-model",
            score=1.0,
            passed=True,
            impossible_detected=True,
            classification="contradictory-requirements",
            conflict_ids={"R1", "R2"},
            explanation_valid=True,
            output_tokens=100,
            input_tokens=50,
            tokens_per_second=10.0,
            ttft_seconds=0.5,
            wall_time_seconds=1.0,
            generated_response="IMPOSSIBLE: yes",
            timestamp="2026-01-01T00:00:00+00:00",
            hardware_label="local",
            connection_type="local",
        )
        d = result.to_dict()
        self.assertIn("task_name", d)
        self.assertIn("task_type", d)
        self.assertIn("score", d)
        self.assertIn("passed", d)


class TestPerformanceMetadata(TestCase):
    """Test 14: performance metadata is returned."""

    def test_result_has_performance_fields(self):
        from src.task_unsolvable import UnsolvableResultData
        result = UnsolvableResultData(
            task_name="Unsolvable Recognition",
            task_type="unsolvable",
            model="test-model",
            score=1.0,
            passed=True,
            impossible_detected=True,
            classification="contradictory-requirements",
            conflict_ids={"R1", "R2"},
            explanation_valid=True,
            output_tokens=500,
            input_tokens=200,
            tokens_per_second=25.0,
            ttft_seconds=0.3,
            wall_time_seconds=2.5,
            generated_response="IMPOSSIBLE: yes",
            timestamp="2026-01-01T00:00:00+00:00",
            hardware_label="local",
            connection_type="local",
        )
        d = result.to_dict()
        self.assertEqual(d["output_tokens"], 500)
        self.assertEqual(d["input_tokens"], 200)
        self.assertEqual(d["tokens_per_second"], 25.0)
        self.assertEqual(d["ttft_seconds"], 0.3)
        self.assertEqual(d["wall_time_seconds"], 2.5)


class TestReasoningMessageExtraction(TestCase):
    """Test 15: reasoning+message response returns only final message."""

    def test_reasoning_plus_message_returns_only_final(self):
        from src.task_unsolvable import normalize_llm_output
        blocks = [
            {"type": "reasoning", "content": "Let me think about this..."},
            {"type": "reasoning", "content": "Actually, I should reconsider..."},
            {"type": "message", "content": "IMPOSSIBLE: yes\nCLASS: contradictory-requirements\nCONFLICT: R1, R2\nEXPLANATION: The requirements demand mutually exclusive outputs for the same input."},
        ]
        result = normalize_llm_output(blocks)
        self.assertNotIn("Let me think", result)
        self.assertIn("IMPOSSIBLE: yes", result)


if __name__ == "__main__":
    main()