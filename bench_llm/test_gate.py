"""Canonical test-gate entry point for Solo Dev BenchLLM (RM-26-AA-0011).

Provides ONE obvious, reproducible way to run each tier of the test taxonomy so
developers and agents never have to guess which subset to run:

    python test_gate.py            # fast local gate (default)
    python test_gate.py full       # authoritative full regression suite
    python test_gate.py targeted <family>

Taxonomy (see docs/architecture.md §"Testing" and docs/ROADMAP.md):

* Fast local gate  -- offline, deterministic tests only. Runs the whole suite's
  collection minus the heavy/integration tiers via ``-m "not slow and not integration and not live"``.
  This auto-includes every new offline test while excluding the deliberately
  tagged-heavy ones. It is broad (imports + read models + benchmark contracts +
  persistence round-trips + ownership/isolation + routing + security policy) but
  bounded, so it is safe to run on every commit / inside an agent loop.
* Full suite       -- ``python -m pytest`` with no marker filter. Authoritative
  broad regression gate. Untouched by this Act; nothing is removed from it.
* Targeted         -- focused runs per benchmark family via explicit file lists.

Exit-code discipline (critical; see Phase 8 of RM-26-AA-0011): pytest is invoked
as a subprocess and its real return code is propagated verbatim through this
process's exit code. Nothing pipes, greps, or otherwise masks it here. A failing
test yields a non-zero exit; "no tests matched" yields pytest's exit code 5 (also
non-zero), so an empty selection can never read as success.

Run from anywhere; the helper chdir()s into its own directory first so ``import
src`` resolves regardless of the caller's working directory.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

_BENCH_DIR = os.path.dirname(os.path.abspath(__file__))

# Fast gate excludes the deliberately heavy / toolchain-dependent / live tiers.
# Everything else (offline + deterministic) is included by default, so new fast
# tests auto-join the gate without per-test bookkeeping.
_FAST_MARKER_EXPR = "not slow and not integration and not live"

# Targeted family runs: explicit file lists (no shell globbing required). Each
# family maps to the high-value files for that surface. These are subsets of the
# full suite; nothing here is exclusive to targeted mode.
_FAMILIES: dict[str, list[str]] = {
    "speed": [
        "tests/test_speed_metrics_act20.py",
        "tests/test_speed_result_routing.py",
        "tests/test_speed_iterations.py",
        "tests/test_v2_speed_suite_runner.py",
    ],
    "workflow": [
        "tests/test_python_validator.py",
        "tests/test_python_validator_evidence_preservation.py",
        "tests/test_java_validator.py",
        "tests/test_markdown_validator_unavailable.py",
        "tests/test_unsolvable_validator.py",
        "tests/test_v2_quality_read_model.py",
        "tests/test_v2_quality_artifact.py",
        "tests/test_v2_quality_suite_runner.py",
        "tests/test_v2_workflow_runner.py",
    ],
    "context": [
        "tests/test_context_corpus.py",
        "tests/test_context_read_model.py",
        "tests/test_context_suite_runner.py",
        "tests/test_context_route.py",
    ],
    "ranking": [
        "tests/test_ranking_read_model.py",
        "tests/test_ranking_route.py",
    ],
    "security": [
        "tests/test_backend_url_policy.py",
        "tests/test_execution_boundary_isolation.py",
        "tests/test_lan_boundary.py",
        "tests/test_java_execution_isolation.py",
    ],
    "persistence": [
        "tests/test_sqlite_storage.py",
        "tests/test_v2_quality_artifact.py",
        "tests/test_model_quantization_persistence.py",
    ],
    "routes": [
        "tests/test_smoke.py",
        "tests/test_context_route.py",
        "tests/test_v2_results_route.py",
        "tests/test_ranking_route.py",
        "tests/test_results_filters_regression.py",
        "tests/test_results_index_null_timestamp.py",
        "tests/test_results_speed_history.py",
    ],
}


def run_pytest(args: list[str]) -> int:
    """Run pytest as a subprocess and return its real exit code (never masked)."""
    cmd = [sys.executable, "-m", "pytest", *args]
    proc = subprocess.run(cmd, cwd=_BENCH_DIR)
    return proc.returncode


def run_fast() -> int:
    """Run the fast local gate over offline/deterministic tests."""
    return run_pytest(["-m", _FAST_MARKER_EXPR])


def run_full() -> int:
    """Run the authoritative full regression suite (all markers included)."""
    return run_pytest([])


def run_targeted(family: str) -> int:
    """Run a focused per-family subset. Unknown family is a hard, visible error."""
    if family not in _FAMILIES:
        known = ", ".join(sorted(_FAMILIES))
        print(f"unknown targeted family {family!r}. Known families: {known}", file=sys.stderr)
        return 2
    return run_pytest(_FAMILIES[family])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="test_gate",
        description="Canonical test gate for Solo Dev BenchLLM (RM-26-AA-0011).",
    )
    parser.add_argument(
        "tier",
        nargs="?",
        default="fast",
        choices=["fast", "full", "targeted"],
        help="which tier to run (default: fast local gate).",
    )
    parser.add_argument(
        "family",
        nargs="?",
        help="targeted family name (speed|workflow|context|ranking|security|persistence|routes).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.tier == "fast":
        print(">> fast local gate (offline/deterministic only)")
        return run_fast()
    if args.tier == "full":
        print(">> full regression suite (authoritative)")
        return run_full()
    if args.tier == "targeted":
        if not args.family:
            print("targeted requires a family name.", file=sys.stderr)
            return 2
        print(f">> targeted: {args.family}")
        return run_targeted(args.family)
    return 2  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
