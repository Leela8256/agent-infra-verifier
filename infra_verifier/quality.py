"""Execute Mithilesh's unchanged checker in an isolated process after measurement."""

import contextlib
import datetime
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

UPSTREAM_COMMIT = "8724c29a311475a01bcc5d37426ed67a13c8b5c5"  # agent-blog-verifier v3.0.0
DEFAULT_VERIFIER = (
    Path(__file__).resolve().parents[1] / "vendor/agent_blog_verifier/verify.py"
)


def evaluate(verifier, output, feed, banned, reference_time):
    command = [
        sys.executable,
        "-m",
        "infra_verifier.quality",
        str(verifier),
        str(output),
        str(feed),
        str(banned),
        reference_time,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if result.returncode:
            return {
                "verdict": "unscored",
                "error": result.stderr[-2000:] or "quality process failed",
            }
        report = json.loads(result.stdout)
        if report.get("verdict") not in {"pass", "partial", "fail"} or not isinstance(
            report.get("checks"), list
        ):
            raise ValueError("quality verifier returned an invalid report")
        return report
    except (subprocess.TimeoutExpired, ValueError, OSError) as error:
        return {"verdict": "unscored", "error": str(error)}


if __name__ == "__main__":
    verifier, output, feed, banned, reference_time = sys.argv[1:]
    spec = importlib.util.spec_from_file_location("upstream_quality", verifier)
    module = importlib.util.module_from_spec(spec)
    # Retain original code; its run() accepts an explicit datetime, unlike its CLI.
    now = datetime.datetime.fromisoformat(reference_time.replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise ValueError("reference_time must include a timezone")
    now = now.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)
        result = module.run(output, feed, banned, now=now)
    print(
        json.dumps({"verdict": result.verdict, "checks": result.rows}, allow_nan=False)
    )
