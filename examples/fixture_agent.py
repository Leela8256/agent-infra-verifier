"""Offline fixture: synthetic token/cost records, real CPU/memory/process consumption."""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from infra_verifier.sdk import Recorder


def write_blog(output, mode):
    topics = ["agent harness", "agent memory", "tool calling"]
    urls = [
        "https://example.test/harness",
        "https://example.test/memory",
        "https://example.test/tools",
    ]
    # Mechanical fixture, deliberately not a claim of readable or grounded prose.
    sections = [
        "# Three Agent Topics\n\n" + " ".join(f"intro{i:03}" for i in range(40))
    ]
    for j, topic in enumerate(topics):
        sections.append(
            f"## {topic.title()}\n\n"
            + " ".join(f"evidence{j}x{i:03}" for i in range(195))
            + f"\n\n[source]({urls[j]})"
        )
    # Checker v3: one section per topic, links inline, no Sources section.
    (output / "blog.md").write_text("\n\n".join(sections))
    if mode != "missing-output":
        (output / "meta.json").write_text(
            json.dumps({"topics": topics, "source_urls": urls})
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=[
            "success",
            "retry",
            "missing-output",
            "timeout",
            "memory",
            "cpu",
            "crash",
            "missing-telemetry",
            "token-budget",
            "orphan",
            "child-memory",
            "malformed-event",
            "unknown-usage",
        ],
    )
    mode = parser.parse_args().mode
    out = Path(os.environ["OUTPUT_DIR"])
    if mode == "timeout":
        time.sleep(30)
    if mode == "memory":
        allocation = bytearray(96 * 2**20)
        time.sleep(10)
        print(len(allocation))
    if mode == "cpu":
        while True:
            sum(i * i for i in range(10000))
    if mode in {"orphan", "child-memory"}:
        code = "import time; block=bytearray(96*2**20); time.sleep(30)"
        child = subprocess.Popen([sys.executable, "-c", code])
        (out / "child.pid").write_text(str(child.pid))
        time.sleep(0.3 if mode == "orphan" else 10)
        return
    if mode == "crash":
        raise RuntimeError("deliberate fixture crash")
    if mode == "missing-telemetry":
        write_blog(out, mode)
        return
    recorder = Recorder()
    if mode == "malformed-event":
        event = {
            "run_id": recorder.run_id,
            "event_id": "bad",
            "kind": "model",
            "name": "fixture",
            "status": "ok",
            "start_offset_s": 0,
            "duration_s": -1,
        }
        req = urllib.request.Request(
            recorder.url,
            data=json.dumps(event).encode(),
            headers={"Authorization": "Bearer " + recorder.token},
        )
        try:
            urllib.request.urlopen(req).close()
        except urllib.error.HTTPError as error:
            error.close()
        return
    recorder.step("search")
    previous = None
    if mode == "retry":
        started = time.monotonic()
        time.sleep(0.01)
        previous = recorder.call(
            "tool", "search", started, status="error", error_type="http_429"
        )
    for page in range(3):
        started = time.monotonic()
        time.sleep(0.01)
        recorder.call(
            "tool", "search", started, retry_of=previous if page == 0 else None
        )
    recorder.step("draft")
    started = time.monotonic()
    time.sleep(0.02)
    recorder.call(
        "model",
        "synthetic-no-provider",
        started,
        tokens_in=None
        if mode == "unknown-usage"
        else (9000 if mode == "token-budget" else 1200),
        tokens_out=700,
        cost_usd=None if mode == "unknown-usage" else 0.004,
    )
    if mode == "token-budget":
        time.sleep(10)
    write_blog(out, mode)
    recorder.step("check-and-fix")
    recorder.finish()


if __name__ == "__main__":
    main()
