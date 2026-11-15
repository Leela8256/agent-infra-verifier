"""Minimal framework-neutral emitter; call at the SDK/tool boundary, once per attempt."""

import json
import os
import time
import urllib.request
import uuid


class Recorder:
    def __init__(self):
        self.url = os.environ["INFRA_EVENT_URL"]
        self.token = os.environ["INFRA_EVENT_TOKEN"]
        self.run_id = os.environ["INFRA_RUN_ID"]
        self.origin = float(os.environ["INFRA_STARTED_MONOTONIC"])

    def emit(self, kind, **data):
        event = {
            "run_id": self.run_id,
            "event_id": str(uuid.uuid4()),
            "kind": kind,
            **data,
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(event, allow_nan=False).encode(),
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            response.read()
        return event["event_id"]

    def call(self, kind, name, started, *, status="ok", retry_of=None, **usage):
        return self.emit(
            kind,
            name=name,
            start_offset_s=max(0, started - self.origin),
            duration_s=time.monotonic() - started,
            status=status,
            retry_of=retry_of,
            **usage,
        )

    def step(self, name):
        return self.emit("step", name=name)

    def finish(self, coverage=("model", "tool", "step")):
        return self.emit("finish", coverage=list(coverage))
