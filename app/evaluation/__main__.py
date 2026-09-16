"""``python -m app.evaluation``: score the rule-based chain on the labelled set."""

import sys

from app.evaluation.dataset import load_cases
from app.evaluation.runner import format_report, run_offline


def main() -> int:
    report = run_offline(load_cases())
    print(format_report(report))
    return 1 if report.leaks else 0  # accuracy may drop; a leak may not


if __name__ == "__main__":
    sys.exit(main())
