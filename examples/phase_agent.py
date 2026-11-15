"""Synthetic stage-evidence fixture; never presented as a real agent implementation."""

import json
import os
import sys
import urllib.error
from pathlib import Path

from fixture_agent import write_blog
from shared_agent import request


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "success"
    assert "LANGFUSE_SECRET_KEY" not in os.environ
    out = Path(os.environ["OUTPUT_DIR"])
    for page in (1, 1, 2, 3):
        try:
            request(os.environ["INFRA_SEARCH_URL"] + f"?page={page}")
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            error.close()
    request(
        os.environ["INFRA_MODEL_BASE_URL"] + "/chat/completions",
        {
            "model": "synthetic-no-provider",
            "messages": [{"role": "user", "content": "synthetic draft"}],
        },
    )
    write_blog(out, "success")
    if mode == "missing":
        return
    if mode == "outside-write":
        try:
            Path(sys.argv[2]).write_text("escape")
        except PermissionError:
            pass
        else:
            raise RuntimeError("write guard allowed escape")
    phases = (
        ("check", "draft", "fix")
        if mode == "wrong-order"
        else ("draft", "check", "fix")
    )
    for phase in phases:
        data = {"phase": phase}
        if phase == "check":
            data["findings"] = ["needs revision"] if mode == "unfixed" else []
        if phase == "fix":
            data["resolution"] = "No changes required after the fixture check"
        response = request(os.environ["INFRA_PHASE_URL"], data)
        print(json.loads(response))


if __name__ == "__main__":
    main()
