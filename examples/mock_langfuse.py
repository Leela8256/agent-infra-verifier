"""Local OTLP transport fixture, not a live Langfuse integration."""

import json
import threading
from http.server import BaseHTTPRequestHandler

from infra_verifier.http_server import LoopbackHTTPServer


class FakeLangfuse:
    def __init__(self, mode="ok"):
        self.mode, self.batches, self.headers = mode, [], []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if self.path != "/api/public/otel/v1/traces":
                    self.send_error(404)
                    return
                owner.batches.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                owner.headers.append({k.lower(): v for k, v in self.headers.items()})
                self.send_response(401 if owner.mode == "auth" else 200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {"partialSuccess": {"rejectedSpans": "1"}}
                        if owner.mode == "partial"
                        else {}
                    ).encode()
                )

        self.server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )

    @property
    def env(self):
        return {
            "LANGFUSE_BASE_URL": f"http://127.0.0.1:{self.server.server_port}",
            "LANGFUSE_PUBLIC_KEY": "fixture-public",
            "LANGFUSE_SECRET_KEY": "fixture-secret",
            "LANGFUSE_PROJECT_ID": "fixture-project",
        }

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
