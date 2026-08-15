"""Tests for src/task_unsolvable.py.

Covers 12 scenarios:

 1. prompt.md loads
 2. scenario.md loads
 3. final prompt contains scenario
 4. string LM Studio output works
 5. list output works
 6. dict output works
 7. correct structured answer -> score 1.0
 8. incorrect answer -> score 0.0
 9. fabricated/code-only response -> fail
10. validator result passes through unchanged
11. performance metadata is returned
12. repeated prompt construction is deterministic
"""

import pytest

# Import the task module
from src import task_unsolvable
from src.unsolvable_validator import UnsolvableResult


# ------------------------------------------------------------------
# 1. prompt.md loads
# ------------------------------------------------------------------

class TestPromptLoads:
    def test_prompt_md_loads(self):
        content = task_unsolvable.load_prompt()
        assert isinstance(content, str)
        assert len(content) > 0

    def test_prompt_contains_instructions(self):
        content = task_unsolvable.load_prompt()
        assert "IMPOSSIBLE" in content
        assert "CLASS" in content
        assert "CONFLICT" in content
        assert "EXPLANATION" in content


# ------------------------------------------------------------------
# 2. scenario.md loads
# ------------------------------------------------------------------

class TestScenarioLoads:
    def test_scenario_md_loads(self):
        content = task_unsolvable.load_scenario()
        assert isinstance(content, str)
        assert len(content) > 0

    def test_scenario_contains_requirements(self):
        content = task_unsolvable.load_scenario()
        assert "R1" in content
        assert "R2" in content
        assert "R3" in content
        assert "R4" in content


# ------------------------------------------------------------------
# 3. Final prompt contains scenario
# ------------------------------------------------------------------

class TestBuildPrompt:
    def test_final_prompt_contains_scenario(self):
        prompt = task_unsolvable.build_unsolvable_prompt()
        scenario = task_unsolvable.load_scenario()
        assert "classify" in prompt
        assert "R1" in prompt
        assert "R2" in prompt

    def test_final_prompt_contains_instructions(self):
        prompt = task_unsolvable.build_unsolvable_prompt()
        assert "IMPOSSIBLE" in prompt
        assert "CLASS" in prompt
        assert "CONFLICT" in prompt
        assert "EXPLANATION" in prompt


# ------------------------------------------------------------------
# 4. String LM Studio output works
# ------------------------------------------------------------------

class TestStringOutput:
    def test_normalize_string(self):
        raw = "IMPOSSIBLE: yes\nCLASS: contradictory-requirements\nCONFLICT: R1, R2\nEXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive, which is a direct contradiction."
        result = task_unsolvable.normalize_llm_output(raw)
        assert result == raw
        assert "IMPOSSIBLE" in result


# ------------------------------------------------------------------
# 5. List output works
# ------------------------------------------------------------------

class TestListOutput:
    def test_normalize_list_of_strings(self):
        raw = ["IMPOSSIBLE: yes", "CLASS: contradictory-requirements", "CONFLICT: R1, R2", "EXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive, which is a direct contradiction."]
        result = task_unsolvable.normalize_llm_output(raw)
        assert "IMPOSSIBLE" in result
        assert "R1" in result

    def test_normalize_list_of_dicts(self):
        raw = [{"content": "IMPOSSIBLE: yes"}, {"content": "CLASS: contradictory-requirements"}, {"content": "CONFLICT: R1, R2"}, {"content": "EXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive, which is a direct contradiction."}]
        result = task_unsolvable.normalize_llm_output(raw)
        assert "IMPOSSIBLE" in result
        assert "R1" in result


# ------------------------------------------------------------------
# 6. Dict output works
# ------------------------------------------------------------------

class TestDictOutput:
    def test_normalize_dict_with_output_key(self):
        raw = {"output": "IMPOSSIBLE: yes\nCLASS: contradictory-requirements\nCONFLICT: R1, R2\nEXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive, which is a direct contradiction."}
        result = task_unsolvable.normalize_llm_output(raw)
        assert "IMPOSSIBLE" in result

    def test_normalize_dict_with_text_key(self):
        raw = {"text": "IMPOSSIBLE: yes\nCLASS: contradictory-requirements\nCONFLICT: R1, R2\nEXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive, which is a direct contradiction."}
        result = task_unsolvable.normalize_llm_output(raw)
        assert "IMPOSSIBLE" in result

    def test_normalize_dict_with_nested_text(self):
        raw = {"output": {"text": "IMPOSSIBLE: yes"}}
        result = task_unsolvable.normalize_llm_output(raw)
        assert "IMPOSSIBLE" in result


# ------------------------------------------------------------------
# 7. Correct structured answer -> score 1.0
# ------------------------------------------------------------------

class TestCorrectAnswer:
    def test_correct_answer_scores_1(self):
        correct_response = (
            "IMPOSSIBLE: yes\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1, R2\n"
            "EXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive, which is a direct contradiction that cannot be resolved."
        )
        validator_result = task_unsolvable.validate_unsolvable_response(correct_response)
        assert validator_result.score == 1.0
        assert validator_result.passed is True
        assert validator_result.impossible_detected is True
        assert "R1" in validator_result.conflict_ids
        assert "R2" in validator_result.conflict_ids
        assert validator_result.explanation_valid is True


# ------------------------------------------------------------------
# 8. Incorrect answer -> score 0.0
# ------------------------------------------------------------------

class TestIncorrectAnswer:
    def test_impossible_no_scores_0(self):
        incorrect_response = (
            "IMPOSSIBLE: no\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1, R2\n"
            "EXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive, which is a direct contradiction that cannot be resolved."
        )
        validator_result = task_unsolvable.validate_unsolvable_response(incorrect_response)
        assert validator_result.score == 0.0
        assert validator_result.passed is False


# ------------------------------------------------------------------
# 9. Fabricated/code-only response -> fail
# ------------------------------------------------------------------

class TestFabricatedResponse:
    def test_code_only_response_fails(self):
        code_response = 'def classify(x):\n    return "zero"'
        validator_result = task_unsolvable.validate_unsolvable_response(code_response)
        assert validator_result.score == 0.0
        assert validator_result.passed is False

    def test_fabricated_solution_fails(self):
        fake_response = (
            "IMPOSSIBLE: no\n"
            "CLASS: solvable\n"
            "SOLUTION: Just make classify always return positive."
        )
        validator_result = task_unsolvable.validate_unsolvable_response(fake_response)
        assert validator_result.score == 0.0
        assert validator_result.passed is False


# ------------------------------------------------------------------
# 10. Validator result passes through unchanged
# ------------------------------------------------------------------

class TestValidatorPassthrough:
    def test_validator_result_preserved(self):
        correct_response = (
            "IMPOSSIBLE: yes\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1, R2\n"
            "EXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive, which is a direct contradiction that cannot be resolved."
        )
        validator_result = task_unsolvable.validate_unsolvable_response(correct_response)
        # Score is preserved exactly
        assert validator_result.score == 1.0
        # All fields are populated
        assert validator_result.impossible_detected is True
        assert validator_result.classification == "contradictory-requirements"
        assert len(validator_result.conflict_ids) == 2
        assert validator_result.explanation_valid is True

    def test_to_dict_serialization(self):
        correct_response = (
            "IMPOSSIBLE: yes\n"
            "CLASS: contradictory-requirements\n"
            "CONFLICT: R1, R2\n"
            "EXPLANATION: Requirement R1 demands classify(0) return zero while R2 demands it return positive, which is a direct contradiction that cannot be resolved."
        )
        validator_result = task_unsolvable.validate_unsolvable_response(correct_response)
        # The result can be serialized
        assert validator_result.score == 1.0
        assert isinstance(validator_result.passed, bool)


# ------------------------------------------------------------------
# 11. Performance metadata is returned
# ------------------------------------------------------------------

class TestPerformanceMetadata:
    def test_result_dataclass_has_all_fields(self):
        """Verify the UnsolvableCorrectnessResult dataclass has all required fields."""
        import inspect
        fields = [f.name for f in task_unsolvable.UnsolvableCorrectnessResult.__dataclass_fields__.values()]
        required_fields = [
            "task_name", "task_type", "model", "score", "passed",
            "impossible_detected", "classification", "conflict_ids",
            "explanation_valid", "output_tokens", "input_tokens",
            "tokens_per_second", "ttft_seconds", "wall_time_seconds",
            "generated_response", "timestamp", "hardware_label",
            "execution_environment", "connection_type", "validator_result",
        ]
        for field in required_fields:
            assert field in fields, f"Missing field: {field}"


# ------------------------------------------------------------------
# 12. Repeated prompt construction is deterministic
# ------------------------------------------------------------------

class TestDeterminism:
    def test_prompt_construction_is_deterministic(self):
        prompt1 = task_unsolvable.build_unsolvable_prompt()
        prompt2 = task_unsolvable.build_unsolvable_prompt()
        assert prompt1 == prompt2

    def test_prompt_contains_all_requirements(self):
        prompt = task_unsolvable.build_unsolvable_prompt()
        assert "R1" in prompt
        assert "R2" in prompt
        assert "R3" in prompt
        assert "R4" in prompt
        assert "classify" in prompt
        assert "zero" in prompt
        assert "positive" in prompt


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

class TestEdgeCases:
    def test_normalize_none_output(self):
        result = task_unsolvable.normalize_llm_output(None)  # type: ignore
        assert result == "None"

    def test_normalize_number_output(self):
        result = task_unsolvable.normalize_llm_output(42)  # type: ignore
        assert result == "42"

    def test_normalize_empty_list(self):
        result = task_unsolvable.normalize_llm_output([])
        assert result == ""

    def test_normalize_empty_dict(self):
        result = task_unsolvable.normalize_llm_output({})
        assert result == "{}"