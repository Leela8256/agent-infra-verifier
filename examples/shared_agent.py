"""HTTP-only fixture. No Recorder calls and no client-side token accounting."""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from fixture_agent import write_blog


def request(url, payload=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": "Bearer " + os.environ["INFRA_PROXY_KEY"],
            "Content-Type": "application/json",
            "X-Infra-Run-ID": os.environ["INFRA_RUN_ID"],
        },
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.read()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "success"
    assert "INFRA_EVENT_TOKEN" not in os.environ
    assert "TEST_PROVIDER_SECRET" not in os.environ
    for page in range(1, 3 if mode == "missing-page" else 4):
        while True:
            try:
                request(os.environ["INFRA_SEARCH_URL"] + f"?page={page}")
                break
            except urllib.error.HTTPError as error:
                status = error.code
                error.close()
                if status != 429:
                    raise
    path = "/responses" if mode.startswith("responses") else "/chat/completions"
    request(
        os.environ["INFRA_MODEL_BASE_URL"] + path,
        {
            "model": "synthetic-no-provider",
            "messages": [{"role": "user", "content": "fixture"}],
            "stream": "stream" in mode,
        },
    )
    if mode == "token-budget":
        time.sleep(10)
    if mode == "outside-write":
        Path("leaked.txt").write_text("outside output")
    write_blog(
        Path(os.environ["OUTPUT_DIR"]),
        "missing-output" if mode == "false-done" else "success",
    )


if __name__ == "__main__":
    main()
