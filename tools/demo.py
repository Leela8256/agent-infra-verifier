"""Run the offline demonstration and retain every evidence bundle."""

import sys
import time
from dataclasses import replace
from pathlib import Path

from infra_verifier.config import Config
from infra_verifier.runner import run, write_json
from infra_verifier.summary import summarize, write_csv

ROOT = Path(__file__).resolve().parents[1]


def main():
    destination = ROOT / "runs" / ("demo-" + time.strftime("%Y%m%d-%H%M%S"))
    destination.mkdir(parents=True)
    base = Config(wall_clock_s=5, memory_mb=256, tokens_in=2000)
    modes = [
        "success",
        "retry",
        "missing-output",
        "timeout",
        "memory",
        "child-memory",
        "cpu",
        "crash",
        "missing-telemetry",
        "token-budget",
        "orphan",
        "malformed-event",
        "unknown-usage",
    ]
    reports = []
    for mode in modes:
        config = base
        if mode == "timeout":
            config = replace(base, wall_clock_s=0.4)
        elif mode in {"memory", "child-memory"}:
            config = replace(base, memory_mb=50)
        elif mode == "cpu":
            config = replace(base, cpu_time_s=0.2)
        directory = destination / mode
        report = run(
            [sys.executable, str(ROOT / "examples/fixture_agent.py"), mode],
            directory,
            config,
            framework="synthetic-fixture",
            telemetry_source="adapter_reported",
            feed=ROOT / "examples/feed.json",
            banned=ROOT / "examples/banned_words.txt",
            experiment=ROOT / "examples/experiment.json",
            reference_time="2026-11-15T00:00:00Z",
        )
        reports.append(directory / "report.json")
        print(
            f"{mode:20} runtime={report['runtime_status']:20} quality={report['quality']['verdict']:8} measurement={report['measurement_status']}",
            flush=True,
        )
    summary = summarize(reports)
    write_json(destination / "summary.json", summary)
    write_csv(destination / "summary.csv", summary)
    print(f"\nEvidence: {destination}")


if __name__ == "__main__":
    main()
