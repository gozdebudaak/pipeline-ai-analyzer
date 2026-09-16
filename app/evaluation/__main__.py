"""``python -m app.evaluation [--llm]``: score the chain on the labelled set.

Without ``--llm`` only the deterministic chain runs (free, seconds). With
``--llm`` every case also goes through the configured model: in APP_ENV=test
that is the fake provider, otherwise the real one and it costs money.
"""

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.evaluation.dataset import load_cases
from app.evaluation.runner import format_report, run_offline, run_with_llm
from app.main import build_analysis_service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation")
    parser.add_argument("--llm", action="store_true", help="also ask the configured LLM")
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.llm:
        service = build_analysis_service(get_settings())
        if service is None:
            print("no LLM provider configured (set OPENAI_API_KEY or APP_ENV=test)")
            return 2
        report = asyncio.run(run_with_llm(cases, service))
    else:
        report = run_offline(cases)

    print(format_report(report))
    return 1 if report.leaks else 0  # accuracy may drop; a leak may not


if __name__ == "__main__":
    sys.exit(main())
