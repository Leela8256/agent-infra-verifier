import argparse
import json
import sys
import uuid
from pathlib import Path

from .config import Config
from .runner import run, write_json
from .summary import summarize, write_csv


def result_exit_code(report):
    if (
        report["measurement_status"] == "invalid"
        or report["runtime_status"]
        in {"launch_error", "supervisor_error", "interrupted"}
        or report["quality"]["verdict"] == "unscored"
    ):
        return 3
    if (
        report["runtime_status"] != "completed"
        or report["budget_status"] == "exceeded"
        or report["quality"]["verdict"] == "fail"
        or any(
            c.get("hard") and c.get("result") == "fail"
            for c in report.get("infra_checks", [])
        )
    ):
        return 2
    if report["measurement_status"] == "incomplete":
        return 3
    return 1 if report["quality"]["verdict"] == "partial" else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Collect infrastructure evidence alongside Mithilesh's quality verifier."
    )
    sub = parser.add_subparsers(dest="action", required=True)
    execute = sub.add_parser("run", help="supervise a trusted local agent command")
    execute.add_argument("--config")
    execute.add_argument("--framework", required=True)
    execute.add_argument("--trial", type=int, default=1)
    execute.add_argument("--run-dir")
    execute.add_argument("--cwd")
    execute.add_argument("--feed")
    execute.add_argument("--banned")
    execute.add_argument("--reference-time")
    execute.add_argument("--quality-verifier")
    execute.add_argument("--trace-url")
    execute.add_argument(
        "--telemetry-source",
        choices=["shared_services", "adapter_reported"],
        default="shared_services",
    )
    execute.add_argument(
        "--model-upstream",
        default="https://openrouter.ai/api/v1",
        help="upstream base URL; proxy is the canonical usage source",
    )
    execute.add_argument(
        "--model-key-env",
        default="OPENROUTER_API_KEY",
        help="environment variable holding the real provider credential (parent only)",
    )
    execute.add_argument(
        "--search-feed", help="frozen feed for shared search; defaults to --feed"
    )
    execute.add_argument(
        "--experiment",
        help="frozen model/provider/settings JSON; hashed into comparison groups",
    )
    execute.add_argument("command", nargs=argparse.REMAINDER)
    summary = sub.add_parser("summarize", help="aggregate saved reports")
    summary.add_argument("reports", nargs="+")
    summary.add_argument("--json", required=True, dest="json_path")
    summary.add_argument("--csv", required=True, dest="csv_path")
    args = parser.parse_args(argv)
    try:
        if args.action == "summarize":
            data = summarize(args.reports)
            write_json(Path(args.json_path), data)
            write_csv(args.csv_path, data)
            print(
                f"Wrote {len(data['rows'])} runs across {data['comparison_groups']} comparison groups."
            )
            return 0
        command = (
            args.command[1:]
            if args.command and args.command[0] == "--"
            else args.command
        )
        config = Config.load(args.config) if args.config else Config()
        report_dir = args.run_dir or str(Path("runs") / str(uuid.uuid4()))
        report = run(
            command,
            report_dir,
            config,
            framework=args.framework,
            trial=args.trial,
            cwd=args.cwd,
            feed=args.feed,
            banned=args.banned,
            reference_time=args.reference_time,
            verifier=args.quality_verifier,
            trace_url=args.trace_url,
            experiment=args.experiment,
            telemetry_source=args.telemetry_source,
            model_upstream=args.model_upstream,
            model_key_env=args.model_key_env,
            search_feed=args.search_feed,
        )
        print(
            json.dumps(
                {
                    "report": str(Path(report_dir).resolve() / "report.json"),
                    "runtime": report["runtime_status"],
                    "measurement": report["measurement_status"],
                    "budget": report["budget_status"],
                    "quality": report["quality"]["verdict"],
                },
                indent=2,
            )
        )
        return result_exit_code(report)
    except (ValueError, OSError) as error:
        print(f"infra-verify: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
