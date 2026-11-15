"""Summaries keep distinct experiment contracts separate."""

import csv
import json
import statistics
from pathlib import Path

from .findings import failure_tags

COLUMNS = [
    "framework",
    "trial",
    "quality_verdict",
    "runtime_status",
    "measurement_status",
    "budget_status",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "wall_clock_s",
    "cpu_time_s",
    "llm_calls",
    "tool_calls",
    "retries",
    "steps",
    "steps_source",
    "errors",
    "retry_recovered",
    "hit_budget_cap",
    "time_to_first_tool_call_s",
    "orphaned_processes",
    "said_done_but_failed",
    "all_search_pages_fetched",
    "search_pages_fetched",
    "writes_outside_output",
    "peak_rss_mb",
    "failure_tags",
    "warning_tags",
    "usage_source",
    "cost_source",
    "trace_url",
    "run_id",
    "comparison_key",
]


def summarize(report_paths):
    reports, ids = [], set()
    for path in report_paths:
        report = json.loads(Path(path).read_text())
        if report["run_id"] in ids:
            raise ValueError(f"duplicate run_id: {report['run_id']}")
        ids.add(report["run_id"])
        reports.append(report)
    rows, groups = [], {}
    for report in reports:
        quality = report["quality"]
        tags, warnings = failure_tags(report)
        rows.append(
            {
                **report["metrics"],
                **{k: report.get(k) for k in COLUMNS if k in report},
                "quality_verdict": quality["verdict"],
                "failure_tags": ";".join(tags),
                "warning_tags": ";".join(warnings),
                "search_pages_fetched": ";".join(
                    map(str, report["metrics"].get("search_pages_fetched") or [])
                ),
            }
        )
        groups.setdefault((report["comparison_key"], report["framework"]), []).append(
            report
        )
    aggregates = []
    for (key, framework), items in groups.items():
        measured = [r for r in items if r["measurement_status"] == "complete"]
        passed = [
            r
            for r in measured
            if r["quality"]["verdict"] == "pass" and r["runtime_status"] == "completed"
        ]
        known_cost = all(
            r["measurement_status"] == "complete"
            and r["metrics"]["cost_usd"] is not None
            for r in items
        )
        total_cost = (
            sum(r["metrics"]["cost_usd"] for r in items) if known_cost else None
        )
        tokens = [
            r["metrics"]["tokens_in"] + r["metrics"]["tokens_out"]
            for r in measured
            if r["metrics"]["tokens_in"] is not None
            and r["metrics"]["tokens_out"] is not None
        ]
        times = [
            r["metrics"]["wall_clock_s"]
            for r in measured
            if r["runtime_status"] == "completed"
        ]
        aggregates.append(
            {
                "framework": framework,
                "comparison_key": key,
                "attempts": len(items),
                "complete_measurements": len(measured),
                "quality_passing_completed_runs": len(passed),
                "total_cost_usd": total_cost,
                "cost_per_passing_run": total_cost / len(passed)
                if total_cost is not None and passed
                else None,
                "tokens_sample_stdev": statistics.stdev(tokens)
                if len(tokens) >= 2
                else None,
                "completed_wall_clock_median_s": statistics.median(times)
                if times
                else None,
                "completed_wall_clock_range_s": [min(times), max(times)]
                if times
                else None,
                "timeouts": sum(
                    "wall_clock_s" in r["budget_violations"] for r in items
                ),
            }
        )
    return {
        "schema_version": 1,
        "rows": rows,
        "aggregates": aggregates,
        "comparison_groups": len({r["comparison_key"] for r in reports}),
        "notes": [
            "Each comparison_key is a separate experiment contract and machine profile.",
            "Null cost means unknown, including when any attempt has incomplete measurements; failed-attempt cost is never silently omitted.",
            "Quality is the upstream verdict. Infrastructure findings do not rewrite it.",
        ],
    }


def write_csv(path, summary):
    with Path(path).open("w", newline="") as target:
        writer = csv.DictWriter(
            target, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(summary["rows"])
