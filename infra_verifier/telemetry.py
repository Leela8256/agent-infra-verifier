"""Authenticated loopback receiver. Events are adapter-reported, not provider attested."""

import hmac
import json
import math
import secrets
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def nonnegative(value, key, integer=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{key} must be a finite nonnegative number")
    if integer and type(value) is not int:
        raise ValueError(f"{key} must be an integer")


class EventStore:
    def __init__(self, path, run_id):
        self.path, self.run_id = path, run_id
        self.events = {}
        self.errors = []
        self.finished = False
        self.coverage = set()
        self.started = None
        self.lock = threading.RLock()
        self.stream = path.open("x", encoding="utf8")

    def invalid(self, reason):
        with self.lock:
            if len(self.errors) < 20:
                self.errors.append(reason)

    def add(self, event):
        with self.lock:
            if not isinstance(event, dict) or event.get("run_id") != self.run_id:
                raise ValueError("event has no matching run_id")
            eid = event.get("event_id")
            if not isinstance(eid, str) or not 1 <= len(eid) <= 128:
                raise ValueError("event_id must be a string of 1-128 characters")
            previous = self.events.get(eid)
            if previous:
                if previous["event"] == event:
                    return  # transport retry: count exactly once
                raise ValueError("conflicting duplicate event_id")
            if self.finished:
                raise ValueError("event received after finish")
            if len(self.events) >= 10000:
                raise ValueError("event limit exceeded")
            kind = event.get("kind")
            if kind not in {"model", "tool", "step", "finish"}:
                raise ValueError("kind must be model, tool, step, or finish")
            if kind in {"model", "tool"}:
                if (
                    not isinstance(event.get("name"), str)
                    or not 1 <= len(event["name"]) <= 256
                ):
                    raise ValueError("name is required")
                if event.get("error_type") is not None and (
                    not isinstance(event["error_type"], str)
                    or len(event["error_type"]) > 256
                ):
                    raise ValueError("error_type must be a string or null")
                if event.get("status") not in {"ok", "error"}:
                    raise ValueError("status must be ok or error")
                nonnegative(event.get("duration_s"), "duration_s")
                nonnegative(event.get("start_offset_s"), "start_offset_s")
                parent_id = event.get("retry_of")
                if parent_id is not None:
                    if not isinstance(parent_id, str):
                        raise ValueError("retry_of must be an event ID string")
                    parent = self.events.get(parent_id, {}).get("event", {})
                    if (
                        parent.get("kind") != kind
                        or parent.get("name") != event["name"]
                        or parent.get("status") != "error"
                    ):
                        raise ValueError(
                            "retry_of must reference a failed attempt of the same kind and name"
                        )
                for key in ("tokens_in", "tokens_out", "cost_usd"):
                    if key in event and event[key] is not None:
                        nonnegative(event[key], key, integer=key != "cost_usd")
            if kind == "finish":
                coverage = event.get("coverage")
                if not isinstance(coverage, list) or any(
                    k not in {"model", "tool", "step"} for k in coverage
                ):
                    raise ValueError(
                        "finish coverage must list model, tool, and/or step"
                    )
                self.coverage = set(coverage)
                self.finished = True
            received = time.monotonic()
            record = {
                "received_offset_s": None
                if self.started is None
                else received - self.started,
                "event": event,
            }
            self.events[eid] = record
            self.stream.write(json.dumps(record, allow_nan=False) + "\n")
            self.stream.flush()

    def metrics(self):
        with self.lock:
            records = list(self.events.values())
            models = [r["event"] for r in records if r["event"]["kind"] == "model"]
            tools = [r["event"] for r in records if r["event"]["kind"] == "tool"]
            calls = models + tools

            def total(key):
                if (
                    "model" not in self.coverage
                    or not self.finished
                    or any(e.get(key) is None for e in models)
                ):
                    return None
                return sum(e[key] for e in models)

            def count(kind):
                n = sum(r["event"]["kind"] == kind for r in records)
                return n if n or (self.finished and kind in self.coverage) else None

            return {
                "tokens_in": total("tokens_in"),
                "tokens_out": total("tokens_out"),
                "cost_usd": total("cost_usd"),
                "llm_calls": count("model"),
                "tool_calls": count("tool"),
                "steps": count("step"),
                "retries": sum(bool(e.get("retry_of")) for e in calls)
                if calls or self.finished
                else None,
                "errors": sum(e["status"] == "error" for e in calls)
                if calls or self.finished
                else None,
                "errors_by_type": dict(
                    Counter(
                        e.get("error_type") or "unspecified"
                        for e in calls
                        if e["status"] == "error"
                    )
                ),
                "tool_calls_by_name": dict(Counter(e["name"] for e in tools)),
                "llm_latency_s": [e["duration_s"] for e in models],
                "models_observed": sorted({e["name"] for e in models}),
                "llm_total_request_s": sum(e["duration_s"] for e in models)
                if models
                else None,
                "tool_total_request_s": sum(e["duration_s"] for e in tools)
                if tools
                else None,
                "time_to_first_tool_call_s": min(
                    (e["start_offset_s"] for e in tools), default=None
                ),
                "retry_recovered": any(
                    e.get("retry_of") and e["status"] == "ok" for e in tools
                ),
                "usage_known_subtotal": {
                    key: sum(e.get(key) or 0 for e in models)
                    for key in ("tokens_in", "tokens_out", "cost_usd")
                },
            }

    def budget_hits(self, config):
        with self.lock:
            m = self.metrics()
            observed = dict(m["usage_known_subtotal"])
            observed.update(
                model_calls=m["llm_calls"], tool_calls=m["tool_calls"], steps=m["steps"]
            )
            return [
                key
                for key, value in observed.items()
                if getattr(config, key) is not None
                and value is not None
                and value > getattr(config, key)
            ]

    def completeness(self):
        with self.lock:
            missing = []
            if not self.finished:
                missing.append("finish event")
            for kind in ("model", "tool", "step"):
                if kind not in self.coverage:
                    missing.append(f"{kind} coverage declaration")
            for key in ("tokens_in", "tokens_out", "cost_usd"):
                if self.metrics()[key] is None:
                    missing.append(key)
            return missing

    def close(self):
        self.stream.close()


class Receiver:
    def __init__(self, store):
        self.store = store
        self.token = secrets.token_urlsafe(32)
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if self.path != "/event":
                    self.send_error(404)
                    return
                if not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + receiver.token
                ):
                    self.send_error(401)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 65536:
                        raise ValueError("event must be 1-65536 bytes")
                    event = json.loads(self.rfile.read(length))
                    store.add(event)
                except (ValueError, TypeError, OSError) as error:
                    store.invalid(str(error))
                    self.send_error(400, "invalid event")
                    return
                self.send_response(204)
                self.end_headers()

            def setup(self):
                super().setup()
                self.connection.settimeout(3)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )

    def start(self):
        self.thread.start()

    @property
    def environment(self):
        return {
            "INFRA_EVENT_URL": f"http://127.0.0.1:{self.server.server_port}/event",
            "INFRA_EVENT_TOKEN": self.token,
            "INFRA_RUN_ID": self.store.run_id,
        }

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
