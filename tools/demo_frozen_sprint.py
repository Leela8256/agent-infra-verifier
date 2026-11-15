"""Run the filled sample against local fake services; no real agent/model grade."""

import json
import os
import platform
import sys
import time
from pathlib import Path
from unittest.mock import patch

from infra_verifier.cli import result_exit_code
from infra_verifier.protocol import freeze, run_trial
from infra_verifier.runner import write_json
from infra_verifier.summary import summarize, write_csv

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0, str(ROOT))
    from examples.mock_langfuse import FakeLangfuse
    from examples.mock_provider import MockProvider

    destination = ROOT / "runs" / ("frozen-demo-" + time.strftime("%Y%m%d-%H%M%S"))
    destination.mkdir(parents=True, exist_ok=False)
    spec_path = ROOT / "experiment/sprint.sample.json"
    spec = json.loads(spec_path.read_text())
    spec["paths"] = {
        key: str((spec_path.parent / value).resolve())
        for key, value in spec["paths"].items()
    }
    spec["execution_host"] = {
        "system": platform.system(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
    }
    reports = []
    with (
        MockProvider() as provider,
        FakeLangfuse() as tracing,
        patch.dict(os.environ, tracing.env),
    ):
        spec.update(
            status="ready",
            model_upstream=provider.url,
            langfuse_base_url=tracing.env["LANGFUSE_BASE_URL"],
        )
        write_json(destination / "spec.json", spec)
        freeze(destination / "spec.json", destination / "bundle")
        print(
            "SYNTHETIC SAMPLE ONLY: local fake model/Langfuse; model rubric remains pending."
        )
        for trial in (1, 2, 3):
            report = run_trial(
                destination / "bundle",
                "fixture",
                trial,
                destination / "trials",
                [sys.executable, str(ROOT / "examples/phase_agent.py")],
            )
            if result_exit_code(report):
                raise AssertionError(
                    f"sample trial {trial} failed; inspect {destination}"
                )
            reports.append(
                destination
                / "trials"
                / spec["sprint_id"]
                / "fixture"
                / f"trial-{trial}"
                / "report.json"
            )
            print(
                f"trial={trial} status={report['trial_status']} export={report['langfuse_export']['status']}",
                flush=True,
            )
    summary = summarize(reports)
    write_json(destination / "summary.json", summary)
    write_csv(destination / "summary.csv", summary)
    print(f"Evidence: {destination}")


if __name__ == "__main__":
    main()
