"""Exercise the sprint path with actual HTTP traffic and an offline provider."""

import sys
import time
from pathlib import Path

from infra_verifier.config import Config
from infra_verifier.runner import run, write_json
from infra_verifier.summary import summarize, write_csv

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0, str(ROOT))
    from examples.mock_provider import MockProvider

    destination = ROOT / "runs" / ("shared-" + time.strftime("%Y%m%d-%H%M%S"))
    reports = []
    with MockProvider() as provider:
        for mode in (
            "success",
            "stream",
            "responses",
            "responses-stream",
            "missing-page",
            "outside-write",
            "false-done",
            "token-budget",
        ):
            directory = destination / mode
            report = run(
                [sys.executable, str(ROOT / "examples/shared_agent.py"), mode],
                directory,
                Config(
                    wall_clock_s=5, tokens_in=1000 if mode == "token-budget" else 5000
                ),
                framework="shared-http-fixture",
                model_upstream=provider.url,
                feed=ROOT / "examples/feed.json",
                banned=ROOT / "examples/banned_words.txt",
                reference_time="2026-11-15T00:00:00Z",
                experiment=ROOT / "examples/experiment.json",
            )
            reports.append(directory / "report.json")
            print(
                f"{mode:20} quality={report['quality']['verdict']:5} runtime={report['runtime_status']:16} hard={report['failure_tags']}",
                flush=True,
            )
    summary = summarize(reports)
    write_json(destination / "summary.json", summary)
    write_csv(destination / "summary.csv", summary)
    print(f"Evidence: {destination}")


if __name__ == "__main__":
    main()
