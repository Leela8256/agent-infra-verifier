"""Replayable OTLP/HTTP JSON export from host evidence; no Langfuse SDK in agents."""

import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .services import NoRedirect, validate_upstream


def attribute(key, value):
    if isinstance(value, bool):
        value = {"boolValue": value}
    elif isinstance(value, int):
        value = {"intValue": str(value)}
    elif isinstance(value, float):
        value = {"doubleValue": value}
    else:
        value = {"stringValue": value if isinstance(value, str) else json.dumps(value)}
    return {"key": key, "value": value}


def payloads(run_dir):
    root = Path(run_dir)
    report = json.loads((root / "report.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    trace_id = hashlib.sha256(report["run_id"].encode()).hexdigest()[:32]
    root_id = hashlib.sha256((report["run_id"] + ":root").encode()).hexdigest()[:16]
    origin = int(datetime.fromisoformat(manifest["started_at"]).timestamp() * 1e9)
    common = {
        "langfuse.trace.name": report["framework"],
        "langfuse.session.id": report.get("sprint_id") or report["comparison_key"],
        "langfuse.trace.metadata.run_id": report["run_id"],
        "langfuse.trace.metadata.framework": report["framework"],
        "langfuse.trace.metadata.trial": report["trial"],
        "langfuse.version": manifest["verifier_version"],
        "langfuse.trace.metadata.usage_source": report["usage_source"],
    }

    def span(sid, name, offset, duration, fields, parent=root_id, error=False):
        result = {
            "traceId": trace_id,
            "spanId": sid,
            "name": name,
            "kind": 1,
            "startTimeUnixNano": str(origin + int(offset * 1e9)),
            "endTimeUnixNano": str(origin + int((offset + duration) * 1e9)),
            "attributes": [attribute(k, v) for k, v in {**common, **fields}.items()],
            "status": {"code": 2 if error else 1},
        }
        if parent:
            result["parentSpanId"] = parent
        return result

    spans = [
        span(
            root_id,
            report["framework"],
            0,
            report["metrics"]["wall_clock_s"],
            {
                "langfuse.observation.type": "agent",
                "langfuse.observation.output": {
                    "quality": report["quality"]["verdict"],
                    "runtime": report["runtime_status"],
                    "failure_tags": report["failure_tags"],
                },
            },
            parent=None,
            error=report["runtime_status"] != "completed",
        )
    ]
    for line in (root / "events.jsonl").read_text().splitlines():
        event = json.loads(line)["event"]
        if event["kind"] not in {"model", "tool", "phase"}:
            continue
        fields = {
            "langfuse.observation.type": {
                "model": "generation",
                "tool": "tool",
                "phase": "span",
            }[event["kind"]],
            "langfuse.observation.metadata.source": event.get("source", "adapter"),
            "langfuse.observation.metadata.retry_of": event.get("retry_of") or "",
            "langfuse.observation.status_message": event.get("error_type") or "",
        }
        if event["kind"] == "model":
            if "request_json" in event:
                fields["langfuse.observation.input"] = event["request_json"]
                fields["langfuse.observation.output"] = event.get("response_text", "")
                fields["langfuse.observation.metadata.io_truncated"] = bool(
                    event.get("request_truncated") or event.get("response_truncated")
                )
            fields["langfuse.observation.model.name"] = event["name"]
            fields["langfuse.observation.usage_details"] = {
                k: event[v]
                for k, v in (("input", "tokens_in"), ("output", "tokens_out"))
                if event.get(v) is not None
            }
            if event.get("cost_usd") is not None:
                fields["langfuse.observation.cost_details"] = {
                    "total": event["cost_usd"]
                }
        elif event["kind"] == "tool":
            fields["langfuse.observation.output"] = {
                "page": event.get("page"),
                "http_status": event.get("http_status"),
            }
        else:
            evidence = root / "phase-evidence" / event["evidence_file"]
            fields["langfuse.observation.output"] = json.loads(evidence.read_text())
        sid = hashlib.sha256(event["event_id"].encode()).hexdigest()[:16]
        spans.append(
            span(
                sid,
                event["name"],
                event.get("start_offset_s", 0),
                event.get("duration_s", 0),
                fields,
                error=event.get("status") == "error",
            )
        )
    # Bound both count and serialized bytes, including artifact snapshots.
    chunks, chunk, size = [], [], 0
    for item in spans:
        item_size = len(json.dumps(item).encode())
        if chunk and (len(chunk) >= 25 or size + item_size > 3 * 1024 * 1024):
            chunks.append(chunk)
            chunk, size = [], 0
        chunk.append(item)
        size += item_size
    if chunk:
        chunks.append(chunk)
    batches = [
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            attribute("service.name", "agent-infra-verifier")
                        ]
                    },
                    "scopeSpans": [
                        {"scope": {"name": "infra-verifier"}, "spans": chunk}
                    ],
                }
            ]
        }
        for chunk in chunks
    ]
    return trace_id, batches


def settings():
    base = os.environ.get("LANGFUSE_BASE_URL", "").rstrip("/")
    public, secret, project = (
        os.environ.get(k, "")
        for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_PROJECT_ID")
    )
    if not all((base, public, secret, project)):
        raise ValueError(
            "set LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_PROJECT_ID"
        )
    validate_upstream(base)
    return base, public, secret, project


def export(run_dir):
    root = Path(run_dir)
    trace_id, batches = payloads(root)
    (root / "langfuse-otlp.json").write_text(json.dumps(batches, indent=2) + "\n")
    status = {
        "status": "failed",
        "trace_id": trace_id,
        "trace_url": None,
        "accepted_batches": 0,
        "total_batches": len(batches),
        "visibility": "not_checked",
    }
    try:
        base, public, secret, project = settings()
        auth = base64.b64encode((public + ":" + secret).encode()).decode()
        parsed_base = validate_upstream(base)
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(
                {}
                if parsed_base.hostname in {"127.0.0.1", "localhost", "::1"}
                else urllib.request.getproxies_environment()
            ),
            NoRedirect,
        )
        for batch in batches:
            req = urllib.request.Request(
                base + "/api/public/otel/v1/traces",
                data=json.dumps(batch).encode(),
                headers={
                    "Authorization": "Basic " + auth,
                    "Content-Type": "application/json",
                    "x-langfuse-ingestion-version": "4",
                },
            )
            with opener.open(req, timeout=15) as response:
                if response.status != 200:
                    raise ValueError(f"Langfuse returned HTTP {response.status}")
                body = response.read(1024 * 1024)
            parsed = json.loads(body) if body else {}
            if not isinstance(parsed, dict):
                raise ValueError("Langfuse returned an invalid OTLP response")
            partial = parsed.get("partialSuccess", {})
            if not isinstance(partial, dict):
                raise ValueError("Langfuse returned invalid partial-success details")
            if partial.get("rejectedSpans", 0) not in (0, "0") or partial.get(
                "errorMessage"
            ):
                raise ValueError(
                    "Langfuse reported partial ingestion; retain and replay evidence"
                )
            status["accepted_batches"] += 1
        status.update(
            status="accepted", trace_url=f"{base}/project/{project}/traces/{trace_id}"
        )
    except (ValueError, OSError, urllib.error.URLError) as error:
        # Avoid recording server bodies, URL errors, or authentication material.
        status["error"] = (
            str(error) if isinstance(error, ValueError) else type(error).__name__
        )
        if isinstance(error, urllib.error.HTTPError):
            status["http_status"] = error.code
            error.close()
    (root / "langfuse-export.json").write_text(json.dumps(status, indent=2) + "\n")
    return status
