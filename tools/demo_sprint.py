"""Offline end-to-end sprint evidence, including expected failure cases."""

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

from infra_verifier.cli import result_exit_code
from infra_verifier.config import Config
from infra_verifier.runner import run, write_json
from infra_verifier.summary import summarize, write_csv

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0, str(ROOT))
    from examples.mock_langfuse import FakeLangfuse
    from examples.mock_provider import MockProvider

    destination = ROOT / "runs" / ("sprint-demo-" + time.strftime("%Y%m%d-%H%M%S"))
    reports = []
    scenarios = {
        "success": ("success", "ok", 0),
        "missing-phases": ("missing", "ok", 3),
        "wrong-order": ("wrong-order", "ok", 2),
        "unfixed-findings": ("unfixed", "ok", 2),
        "denied-outside-write": ("outside-write", "ok", 0),
        "partial-upload": ("success", "partial", 3),
        "upload-auth-failure": ("success", "auth", 3),
    }
    with MockProvider() as provider:
        for name, (mode, trace_mode, expected_exit) in scenarios.items():
            directory = destination / name
            with (
                FakeLangfuse(trace_mode) as tracing,
                patch.dict(os.environ, tracing.env),
            ):
                report = run(
                    [
                        sys.executable,
                        str(ROOT / "examples/phase_agent.py"),
                        mode,
                        str(destination / "forbidden.txt"),
                    ],
                    directory,
                    Config(wall_clock_s=5, cost_usd=1, steps=50),
                    framework="synthetic-sprint-fixture",
                    model_upstream=provider.url,
                    feed=ROOT / "examples/feed.json",
                    banned=ROOT / "examples/banned_words.txt",
                    task_prompt=ROOT / "experiment/task.prompt.txt",
                    reference_time="2026-11-15T00:00:00Z",
                    experiment=ROOT / "examples/experiment.json",
                    require_phases=True,
                    langfuse="required",
                    filesystem_mode="enforce",
                    enforce_model_settings=True,
                )
            actual = result_exit_code(report)
            if actual != expected_exit:
                raise AssertionError(
                    f"{name}: expected exit {expected_exit}, got {actual}"
                )
            reports.append(directory / "report.json")
            print(
                f"{name:24} workflow={report['workflow']['status']:16} "
                f"export={report['langfuse_export']['status']:8} exit={actual}",
                flush=True,
            )
    summary = summarize(reports)
    write_json(destination / "summary.json", summary)
    write_csv(destination / "summary.csv", summary)
    print(f"Evidence: {destination}")


if __name__ == "__main__":
    main()
