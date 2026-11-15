"""Offline upstream for transport/accounting tests, not a real language model."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockProvider:
    def __init__(self, mode="normal"):
        self.requests = []
        self.mode = mode
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(
                    {
                        "path": self.path,
                        "run_id": self.headers.get("X-Infra-Run-ID"),
                        "authorization": self.headers.get("Authorization"),
                        "body": data,
                    }
                )
                usage = {"prompt_tokens": 1200, "completion_tokens": 700, "cost": 0.004}
                if owner.mode == "missing-cost":
                    usage.pop("cost")
                if owner.mode == "invalid-usage":
                    usage["prompt_tokens"] = -1
                if self.path.endswith("responses"):
                    usage = {"input_tokens": 1200, "output_tokens": 700, "cost": 0.004}
                status = (
                    429 if owner.mode == "retry" and len(owner.requests) == 1 else 200
                )
                if owner.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", owner.url + "/stolen")
                    self.end_headers()
                    return
                self.send_response(status)
                self.send_header(
                    "Content-Type",
                    "text/event-stream" if data.get("stream") else "application/json",
                )
                self.end_headers()
                if status == 429:
                    self.wfile.write(
                        json.dumps({"error": {"message": "retry"}}).encode()
                    )
                    return
                result = {
                    "id": "fixture-request",
                    "model": data["model"],
                    "usage": usage,
                    "choices": [],
                }
                if data.get("stream"):
                    if self.path.endswith("responses"):
                        result = {"type": "response.completed", "response": result}
                    if owner.mode == "stream-error":
                        result["error"] = {"message": "upstream failed"}
                    self.wfile.write(b"data: " + json.dumps(result).encode() + b"\n\n")
                    if owner.mode != "truncated":
                        self.wfile.write(b"data: [DONE]\n\n")
                else:
                    self.wfile.write(json.dumps(result).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
