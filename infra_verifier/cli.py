import argparse
import json
import os
import sys
import urllib.request
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
        or (
            report.get("requires_langfuse")
            and report.get("langfuse_export", {}).get("status") != "accepted"
        )
        or (
            report.get("requires_phase_evidence")
            and report.get("workflow", {}).get("status") == "missing_evidence"
        )
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
    preflight = sub.add_parser("preflight", help="list unresolved sprint decisions")
    preflight.add_argument("--spec", required=True)
    freezing = sub.add_parser(
        "freeze", help="create an immutable input bundle after decisions are complete"
    )
    freezing.add_argument("--spec", required=True)
    freezing.add_argument("--bundle", required=True)
    sprint = sub.add_parser(
        "sprint-run",
        help="run one of the three recorded trials against a frozen bundle",
    )
    sprint.add_argument("--bundle", required=True)
    sprint.add_argument("--framework", required=True)
    sprint.add_argument("--trial", type=int, choices=[1, 2, 3], required=True)
    sprint.add_argument("--run-root", default="runs")
    sprint.add_argument("command", nargs=argparse.REMAINDER)
    exporting = sub.add_parser(
        "export-langfuse", help="replay trace export without rerunning the agent"
    )
    exporting.add_argument("run_dir")
    phase = sub.add_parser(
        "phase",
        help="capture an artifact-backed phase checkpoint from inside the agent",
    )
    phase.add_argument("name", choices=["draft", "check", "fix"])
    phase.add_argument("--findings-json", default="null")
    phase.add_argument("--resolution")
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
    execute.add_argument("--task-prompt")
    execute.add_argument(
        "--filesystem-mode", choices=["audit", "enforce"], default="audit"
    )
    execute.add_argument("--require-phases", action="store_true")
    execute.add_argument(
        "--phase-evidence",
        choices=["declared", "declared_or_observed"],
        default="declared",
        help="v2: accept host-observed blog.md revisions when no stages are declared",
    )
    execute.add_argument("--langfuse", choices=["off", "required"], default="off")
    execute.add_argument("--request-timeout", type=float, default=15)
    execute.add_argument("--enforce-model-settings", action="store_true")
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
        if args.action == "preflight":
            from .protocol import inspect_spec

            _, result, _, _ = inspect_spec(args.spec)
            print(json.dumps(result, indent=2))
            return 0 if result["ready"] else 3
        if args.action == "freeze":
            from .protocol import freeze

            print(json.dumps(freeze(args.spec, args.bundle), indent=2))
            return 0
        if args.action == "sprint-run":
            from .protocol import run_trial

            command = (
                args.command[1:]
                if args.command and args.command[0] == "--"
                else args.command
            )
            report = run_trial(
                args.bundle, args.framework, args.trial, args.run_root, command
            )
            print(
                json.dumps(
                    {
                        "run_id": report["run_id"],
                        "trace_url": report["trace_url"],
                        "trial_status": report["trial_status"],
                    },
                    indent=2,
                )
            )
            return result_exit_code(report)
        if args.action == "export-langfuse":
            from .langfuse_export import export

            result = export(args.run_dir)
            report_path = Path(args.run_dir) / "report.json"
            report = json.loads(report_path.read_text())
            report.update(langfuse_export=result, trace_url=result.get("trace_url"))
            if "trial_status" in report:
                report["trial_status"] = (
                    "recorded_with_findings"
                    if result_exit_code(report)
                    else "awaiting_model_rubric_review"
                )
            write_json(report_path, report)
            print(json.dumps(result, indent=2))
            return 0 if result["status"] == "accepted" else 3
        if args.action == "phase":
            url, key = (
                os.environ.get("INFRA_PHASE_URL"),
                os.environ.get("INFRA_PROXY_KEY"),
            )
            if not url or not key:
                raise ValueError(
                    "phase must run inside a supervised shared-service agent"
                )
            payload = {
                "phase": args.name,
                "findings": json.loads(args.findings_json),
                "resolution": args.resolution,
            }
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + key,
                },
            )
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                request, timeout=10
            ) as response:
                print(response.read().decode())
            return 0
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
            task_prompt=args.task_prompt,
            filesystem_mode=args.filesystem_mode,
            require_phases=args.require_phases,
            langfuse=args.langfuse,
            request_timeout_s=args.request_timeout,
            enforce_model_settings=args.enforce_model_settings,
            phase_evidence=args.phase_evidence,
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
