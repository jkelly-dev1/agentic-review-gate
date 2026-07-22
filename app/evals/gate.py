"""Eval gate CLI — the thing CI calls.

Runs the golden set and exits non-zero when the pass rate falls below the
threshold. This is what turns "we have evals" into "a regression cannot merge".

    python -m app.evals.gate            # uses EVAL_PASS_THRESHOLD (default 0.80)
    python -m app.evals.gate --threshold 0.9
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.evals.runner import run_golden


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Eval gate for the review agent.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=settings.eval_pass_threshold,
        help="minimum acceptable pass rate (0..1)",
    )
    args = parser.parse_args(argv)

    report = run_golden()
    print("Eval gate:", report.summary(), f"threshold={args.threshold}")
    for c in report.cases:
        mark = "PASS" if c.passed else "FAIL"
        print(f"  [{mark}] {c.id:16s} score={c.score} checks={c.checks}")

    if report.pass_rate < args.threshold:
        print(f"GATE FAILED: pass_rate {report.pass_rate} < threshold {args.threshold}")
        return 1
    print(f"GATE PASSED: pass_rate {report.pass_rate} >= threshold {args.threshold}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
