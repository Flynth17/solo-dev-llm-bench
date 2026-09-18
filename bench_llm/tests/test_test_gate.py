"""Exit-code discipline tests for the canonical test gate (RM-26-AA-0011, Phase 8).

Proves the fast gate cannot produce a false green: pytest's real return code is
propagated verbatim by the helper. These tests invoke pytest on *explicit* file
lists only -- never with an unfiltered marker expression -- so they cannot recurse
into themselves.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_gate as tg  # noqa: E402


def test_passing_subset_returns_zero():
    """A known-fast, offline file must yield exit code 0 (no false negative)."""
    assert tg.run_pytest(["tests/test_smoke.py"]) == 0


def test_failing_subset_returns_nonzero_no_false_green(tmp_path):
    """A failing test must surface as a non-zero exit -- the gate's core promise."""
    failing = tmp_path / "test_definitely_fails.py"
    failing.write_text("def testboom():\n    assert False, 'intentional failure'\n")
    # Absolute path; the helper chdir()s to bench_llm but pytest accepts abs paths.
    code = tg.run_pytest([str(failing)])
    assert code != 0


def test_empty_selection_is_nonzero():
    """'No tests matched' (pytest exit 5) must NOT read as success."""
    code = tg.run_pytest(["tests/test_smoke.py", "-k", "zzz_no_match_zzz"])
    assert code != 0


def test_unknown_targeted_family_errors_visible():
    """An unknown targeted family is a hard, visible error (exit 2), not silence."""
    assert tg.main(["targeted", "does_not_exist"]) == 2
