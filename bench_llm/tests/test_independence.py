"""Tests for independence between the Python and Unsolvable *validators*.

These verify that the shared standalone validators carry no cross-referencing
mutable state and return distinct result types -- an invariant of the active,
retained validation layer (the legacy combined-evaluation route that once drove
these runners was retired in Act 24).
"""

import inspect

import pytest


# ------------------------------------------------------------------
# Test: validator independence (active validators)
# ------------------------------------------------------------------

class TestValidatorIndependence:
    """Ensure the two validators don't share any mutable global state."""

    def test_python_validator_no_unsolvable_state(self):
        """Python validator must not import or reference unsolvable module."""
        from src.python_validator import validate_python_solution, PythonValidationResult
        source = inspect.getsource(validate_python_solution)
        assert "unsolvable" not in source.lower(), \
            "Python validator references unsolvable module -- potential shared state"

    def test_unsolvable_validator_no_python_state(self):
        """Unsolvable validator must not import or reference Python validator."""
        from src.unsolvable_validator import validate_unsolvable_response, UnsolvableResult
        source = inspect.getsource(validate_unsolvable_response)
        assert "python_validator" not in source.lower(), \
            "Unsolvable validator references python_validator -- potential shared state"

    def test_different_result_types(self):
        """Python and Unsolvable must return different result types."""
        from src.python_validator import PythonValidationResult
        from dataclasses import is_dataclass as _is_dataclass

        py_result = PythonValidationResult()
        assert _is_dataclass(py_result), "Python result should be a dataclass"

        # Unsolvable returns an UnsolvableResult (also a dataclass)
        from src.unsolvable_validator import UnsolvableResult
        us_result = UnsolvableResult(score=0.0, passed=False, impossible_detected=False,
                                     classification="", conflict_ids=set(), explanation_valid=False)
        assert _is_dataclass(us_result), "Unsolvable result should be a dataclass"

        # They must be different types
        assert type(py_result) is not type(us_result), \
            "Python and Unsolvable results must have different types"
