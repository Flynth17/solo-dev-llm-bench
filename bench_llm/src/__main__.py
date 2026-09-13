"""Command-line entry point for BenchLLM (package ``python -m src``).

Subcommands::

    python -m src v2-suite <python|java|markdown|evidence|drift>
    python -m src v2-run            # launch all five suites as fresh processes, then combine

The standalone suite runner lives in :mod:`src.v2_quality_suite_runner`. See that
module's docstring for the architecture and immutable-denominator contract.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from src.v2_quality_suite_runner import (  # noqa: E402
    SUITES,
    DEFAULT_LM_STUDIO_URL,
    DEFAULT_MODEL,
    run_suite,
    run_v2_run,
    combine_suite_results,
    ConfigurationMismatchError,
)
from src.v2_speed_suite_runner import run_speed_suite  # noqa: E402


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


def cmd_v2_suite(args: argparse.Namespace) -> int:
    result = asyncio.run(
        run_suite(
            args.suite,
            lm_studio_url=args.lm_studio_url,
            model=args.model,
            run_id=args.run_id,
        )
    )
    payload = result.to_dict()
    text = _dump(payload)
    print(text)
    out_path = os.environ.get("BENCH_SUITE_OUT_PATH")
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    # Non-zero exit when the suite produced no gradeable checks at all -- useful for
    # orchestrators/tests that want to distinguish "ran but failed" from "crashed".
    return 0


def cmd_v2_run(args: argparse.Namespace) -> int:
    try:
        agg = asyncio.run(
            run_v2_run(
                lm_studio_url=args.lm_studio_url,
                model=args.model,
                run_id=args.run_id,
            )
        )
    except ConfigurationMismatchError as exc:
        print(_dump({"error": "configuration_mismatch", "details": str(exc)}), file=sys.stderr)
        return 2
    print(_dump(agg))
    return 0


def cmd_v2_speed(args: argparse.Namespace) -> int:
    try:
        summary = asyncio.run(
            run_speed_suite(
                lm_studio_url=args.lm_studio_url,
                model=args.model,
                hardware_label=args.hardware_label or "",
                run_id=args.run_id,
            )
        )
    except Exception as exc:
        # Concise, non-zero exit signal for orchestrators/tests; never a raw traceback.
        print(_dump({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    print(_dump(summary))
    return 1 if summary.get("status") == "failed" else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src", description="BenchLLM V2 suite runner")
    sub = parser.add_subparsers(dest="command", required=True)

    p_suite = sub.add_parser("v2-suite", help="Run one isolated quality suite in a fresh process.")
    p_suite.add_argument("suite", choices=SUITES)
    p_suite.add_argument("--lm-studio-url", default=DEFAULT_LM_STUDIO_URL)
    p_suite.add_argument("--model", default=DEFAULT_MODEL)
    p_suite.add_argument("--run-id", default=None)
    p_suite.set_defaults(func=cmd_v2_suite)

    p_run = sub.add_parser("v2-run", help="Run all five suites as fresh processes and combine to /166.")
    p_run.add_argument("--lm-studio-url", default=DEFAULT_LM_STUDIO_URL)
    p_run.add_argument("--model", default=DEFAULT_MODEL)
    p_run.add_argument("--run-id", default=None)
    p_run.set_defaults(func=cmd_v2_run)

    p_speed = sub.add_parser(
        "v2-speed",
        help="Run the fixed standard Speed suite (8K / 16K / 32K) as a fresh process.",
    )
    p_speed.add_argument("--lm-studio-url", default=DEFAULT_LM_STUDIO_URL)
    p_speed.add_argument("--model", default=DEFAULT_MODEL)
    p_speed.add_argument("--run-id", default=None)
    p_speed.add_argument("--hardware-label", default="")
    p_speed.set_defaults(func=cmd_v2_speed)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
