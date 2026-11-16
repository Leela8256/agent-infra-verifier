"""Per-run, host-recorded model proxy and frozen three-page search service."""

import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler

from .http_server import LoopbackHTTPServer
from .telemetry import nonnegative


def reject_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_upstream(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "model upstream must not contain credentials, query, or fragment"
        )
    if not parsed.hostname or (
        parsed.scheme != "https"
        and not (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        )
    ):
        raise ValueError(
            "model upstream requires HTTPS (HTTP allowed only on loopback)"
        )
    return parsed


class SharedServices:
    def __init__(
        self,
        store,
        feed,
        upstream,
        api_key,
        *,
        timeout=15,
        phases=None,
        model_settings=None,
    ):
        parsed = validate_upstream(upstream)
        if not isinstance(feed, list) or len(feed) < 3:
            raise ValueError("shared search requires at least three feed records")
        self.store, self.feed = store, feed
        self.phases, self.model_settings = phases, model_settings
        self.upstream, self.api_key, self.timeout = (
            upstream.rstrip("/"),
            api_key,
            timeout,
        )
        self.token = secrets.token_urlsafe(32)
        # Never send local fixture traffic through system proxy auto-discovery.
        # In particular, macOS PAC discovery can block a new supervisor process.
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
            if parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            else urllib.request.ProxyHandler(urllib.request.getproxies_environment()),
            NoRedirect,
        )
        self.lock = threading.RLock()
        self.pending, self.failed = {}, {}
        self.pages = set()
        self.search_attempts = 0
        self.closed = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(owner.timeout)

            def reply(self, status, data, headers=None):
                content = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(content)

            def authenticated(self):
                if not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + owner.token
                ):
                    self.reply(401, {"error": "invalid per-run credential"})
                    return False
                supplied = self.headers.get("X-Infra-Run-ID")
                if supplied and supplied != owner.store.run_id:
                    self.reply(403, {"error": "run ID mismatch"})
                    return False
                if owner.closed:
                    self.reply(503, {"error": "run closed"})
                    return False
                return True

            def do_GET(self):
                if not self.authenticated():
                    return
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path != "/search":
                    self.reply(404, {"error": "unknown endpoint"})
                    return
                query = urllib.parse.parse_qs(parsed.query)
                owner.search(self, {"page": query.get("page", ["1"])[0]})

            def do_POST(self):
                if not self.authenticated():
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4 * 1024 * 1024:
                        raise ValueError("request body must be 1 byte to 4 MiB")
                    data = json.loads(
                        self.rfile.read(length), parse_constant=reject_constant
                    )
                    if not isinstance(data, dict):
                        raise ValueError("request must be a JSON object")
                except (ValueError, OSError):
                    self.reply(400, {"error": "invalid JSON request"})
                    return
                if self.path == "/search":
                    owner.search(self, data)
                elif self.path == "/phase" and owner.phases is not None:
                    try:
                        with owner.lock:
                            result = owner.phases.capture(data)
                        self.reply(200, result)
                    except (ValueError, OSError) as error:
                        self.reply(400, {"error": str(error)})
                elif self.path in {"/v1/chat/completions", "/v1/responses"}:
                    owner.model(self, data)
                else:
                    self.reply(
                        404,
                        {
                            "error": "supported endpoints: /search, /v1/chat/completions, /v1/responses"
                        },
                    )

        self.server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
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
        base = f"http://127.0.0.1:{self.server.server_port}"
        return {
            "INFRA_RUN_ID": self.store.run_id,
            "INFRA_MODEL_BASE_URL": base + "/v1",
            "OPENAI_BASE_URL": base + "/v1",
            "OPENAI_API_KEY": self.token,
            "OPENROUTER_API_KEY": self.token,
            "OPENROUTER_BASE_URL": base + "/v1",
            "INFRA_PROXY_KEY": self.token,
            "INFRA_SEARCH_URL": base + "/search",
            "INFRA_PHASE_URL": base + "/phase",
        }

    def begin(self, kind, name, fingerprint, **extra):
        with self.lock:
            if self.closed:
                return None
            event = {
                "run_id": self.store.run_id,
                "event_id": str(uuid.uuid4()),
                "kind": kind,
                "name": name,
                "status": "error",
                "start_offset_s": max(0, time.monotonic() - self.store.started),
                "duration_s": 0,
                "retry_of": self.failed.pop(fingerprint, None),
                "source": "model_proxy" if kind == "model" else "mock_search",
                **extra,
            }
            self.pending[event["event_id"]] = (event, fingerprint, time.monotonic())
            if kind == "model":
                self.store.add(
                    {
                        "run_id": self.store.run_id,
                        "event_id": str(uuid.uuid4()),
                        "kind": "step",
                        "name": "model_request_attempt",
                        "source": "model_proxy",
                    }
                )
            return event

    def finish(self, event, **fields):
        with self.lock:
            if event is None or event["event_id"] not in self.pending:
                return
            _, fingerprint, started = self.pending.pop(event["event_id"])
            event.update(fields, duration_s=time.monotonic() - started)
            if event["status"] == "error":
                self.failed[fingerprint] = event["event_id"]
            try:
                self.store.add(dict(event))
            except (ValueError, TypeError) as error:
                self.store.invalid("shared service event: " + str(error))

    def search(self, handler, data):
        raw = data.get("page", 1)
        page = int(raw) if isinstance(raw, (str, int)) and str(raw).isdigit() else None
        event = self.begin("tool", "mock_search", ("search", page), page=page)
        if event is None:
            handler.reply(503, {"error": "run closed"})
            return
        with self.lock:
            if page not in {1, 2, 3}:
                status, result = 400, {"error": "page must be 1, 2, or 3"}
            else:
                self.search_attempts += 1
                if self.search_attempts == 1:
                    status, result = (
                        429,
                        {"error": "planted rate limit; retry this page"},
                    )
                else:
                    # Exactly three deterministic pages, without filtering or reordering.
                    start, end = (
                        (page - 1) * len(self.feed) // 3,
                        page * len(self.feed) // 3,
                    )
                    status, result = (
                        200,
                        {
                            "results": self.feed[start:end],
                            "page": page,
                            "total_pages": 3,
                            "next_page": page + 1 if page < 3 else None,
                        },
                    )
            try:
                handler.reply(
                    status, result, {"Retry-After": "0"} if status == 429 else None
                )
                if status == 200:
                    self.pages.add(page)
                self.finish(
                    event,
                    status="ok" if status == 200 else "error",
                    http_status=status,
                    error_type=None if status == 200 else f"http_{status}",
                )
            except OSError:
                self.finish(event, error_type="client_disconnect")

    def usage(self, event, payload):
        if self.closed:
            return
        if not isinstance(payload, dict):
            return
        response = payload.get("response") or payload
        if not isinstance(response, dict):
            return
        if response.get("id"):
            event["provider_request_id"] = response["id"]
        if isinstance(response.get("model"), str):
            event["resolved_model"] = response["model"]
        usage = response.get("usage")
        if isinstance(usage, dict):
            for key, names in {
                "tokens_in": ("prompt_tokens", "input_tokens"),
                "tokens_out": ("completion_tokens", "output_tokens"),
                "cost_usd": ("cost",),
            }.items():
                value = next((usage[n] for n in names if n in usage), None)
                if value is not None:
                    try:
                        nonnegative(value, key, integer=key != "cost_usd")
                        event[key] = value
                        if key == "cost_usd":
                            event["cost_source"] = "provider_response_usage.cost"
                    except ValueError:
                        self.store.invalid(f"provider returned invalid {key}")
            # v2: providers such as the Claude API return tokens but no cost. With a frozen
            # price table, derive cost from provider-reported tokens; provider cost still wins.
            prices = (self.model_settings or {}).get("price_per_mtok")
            if (
                prices
                and event.get("cost_source") != "provider_response_usage.cost"
                and event.get("tokens_in") is not None
                and event.get("tokens_out") is not None
            ):
                event["cost_usd"] = round(
                    event["tokens_in"] * prices["input"] / 1e6
                    + event["tokens_out"] * prices["output"] / 1e6,
                    8,
                )
                event["cost_source"] = "frozen_price_table"
        if payload.get("error") or payload.get("type") in {
            "response.failed",
            "response.incomplete",
            "error",
        }:
            event.update(status="error", error_type="provider_error")
        if any(
            c.get("finish_reason") == "error"
            for c in payload.get("choices", [])
            if isinstance(c, dict)
        ):
            event.update(status="error", error_type="provider_error")

    def model(self, handler, data):
        if self.model_settings:
            expected = self.model_settings
            token_key = (
                "max_output_tokens"
                if handler.path.endswith("responses")
                else "max_tokens"
            )
            token_keys = (
                ("max_output_tokens",)
                if token_key == "max_output_tokens"
                else ("max_tokens", "max_completion_tokens")
            )
            mismatches = [
                key
                for key, value in (
                    ("model", expected["model"]),
                    ("temperature", expected["temperature"]),
                    ("reasoning_effort", expected.get("reasoning_effort")),
                )
                # v2: a frozen None means "omit": any explicit value is a conflict.
                if (key in data and value is None and key != "model")
                or (value is not None and data.get(key, value) != value)
            ]
            if any(
                data.get(key, expected["max_tokens"]) != expected["max_tokens"]
                for key in token_keys
            ):
                mismatches.append("max_tokens")
            if mismatches:
                self.store.invalid(
                    "frozen model settings mismatch: " + ", ".join(mismatches)
                )
                handler.reply(
                    409,
                    {
                        "error": "request conflicts with frozen model settings",
                        "fields": mismatches,
                    },
                )
                return
            data = dict(data)
            data["model"] = expected["model"]
            # v2: temperature None = leave it out (models such as Claude Opus 5.5 reject it).
            if expected["temperature"] is None:
                data.pop("temperature", None)
            else:
                data["temperature"] = expected["temperature"]
            if expected.get("reasoning_effort") is not None:
                if handler.path.endswith("responses"):
                    data["reasoning"] = {**(data.get("reasoning") or {}),
                                         "effort": expected["reasoning_effort"]}
                else:
                    data["reasoning_effort"] = expected["reasoning_effort"]
            if token_key == "max_output_tokens" or "max_completion_tokens" not in data:
                data[token_key] = expected["max_tokens"]
        model = data.get("model")
        if not isinstance(model, str) or not 1 <= len(model) <= 256:
            handler.reply(400, {"error": "model is required"})
            return
        body = json.dumps(data, allow_nan=False).encode()
        fingerprint = (
            handler.path,
            hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
        )
        event = self.begin(
            "model", model, fingerprint, tokens_in=None, tokens_out=None, cost_usd=None
        )
        if event is None:
            handler.reply(503, {"error": "run closed"})
            return
        if self.model_settings:
            event["request_json"] = body.decode()[:65536]
            event["request_truncated"] = len(body.decode()) > 65536
        request = urllib.request.Request(
            self.upstream + handler.path.removeprefix("/v1"),
            data=body,
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
                "X-Infra-Run-ID": self.store.run_id,
            },
        )
        deadline = time.monotonic() + self.timeout
        sent = False
        try:
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                event.update(
                    status="ok" if response.status == 200 else "error",
                    http_status=response.status,
                    error_type=None
                    if response.status == 200
                    else f"http_{response.status}",
                )
                stream = "text/event-stream" in response.headers.get("Content-Type", "")
                if response.status != 200 and not stream:
                    # HTTP clients can retry as soon as they see error headers.
                    # Commit the failed attempt before making those headers visible.
                    raw = response.read(64 * 1024 * 1024 + 1)
                    if len(raw) > 64 * 1024 * 1024:
                        raise ValueError("upstream response exceeds capture limit")
                    try:
                        self.usage(event, json.loads(raw))
                    except ValueError:
                        pass  # Preserve the HTTP error and unknown usage.
                    self.finish(event)
                    handler.send_response(response.status)
                    handler.send_header("Content-Type", "application/json")
                    handler.send_header("Content-Length", str(len(raw)))
                    if response.headers.get("Retry-After"):
                        handler.send_header(
                            "Retry-After", response.headers["Retry-After"]
                        )
                    handler.end_headers()
                    sent = True
                    handler.wfile.write(raw)
                    return
                handler.send_response(response.status)
                handler.send_header(
                    "Content-Type",
                    "text/event-stream" if stream else "application/json",
                )
                for name in ("Retry-After", "x-request-id"):
                    if response.headers.get(name):
                        handler.send_header(name, response.headers[name])
                handler.end_headers()
                sent = True
                disconnected = False
                if stream:
                    terminal = False
                    size = 0
                    while True:
                        if time.monotonic() > deadline:
                            raise TimeoutError("upstream deadline")
                        line = response.readline(1024 * 1024 + 1)
                        if not line:
                            break
                        size += len(line)
                        if len(line) > 1024 * 1024 or size > 64 * 1024 * 1024:
                            raise ValueError("upstream stream exceeds capture limit")
                        if line.startswith(b"data:"):
                            if self.model_settings:
                                text = event.get("response_text", "") + line.decode(
                                    "utf8", errors="replace"
                                )
                                event["response_text"] = text[:65536]
                                event["response_truncated"] = (
                                    event.get("response_truncated", False)
                                    or len(text) > 65536
                                )
                            value = line[5:].strip()
                            if value == b"[DONE]":
                                terminal = True
                            elif value:
                                payload = json.loads(value)
                                self.usage(event, payload)
                                if isinstance(payload, dict) and payload.get(
                                    "type"
                                ) in {
                                    "response.completed",
                                    "response.failed",
                                    "response.incomplete",
                                }:
                                    terminal = True
                        if not disconnected:
                            try:
                                handler.wfile.write(line)
                                handler.wfile.flush()
                            except OSError:
                                disconnected = True
                    if not terminal:
                        event.update(status="error", error_type="truncated_stream")
                else:
                    raw = response.read(64 * 1024 * 1024 + 1)
                    if len(raw) > 64 * 1024 * 1024:
                        raise ValueError("upstream response exceeds capture limit")
                    if self.model_settings:
                        event["response_text"] = raw.decode("utf8", errors="replace")[
                            :65536
                        ]
                        event["response_truncated"] = len(raw) > 65536
                    try:
                        self.usage(event, json.loads(raw))
                    except ValueError:
                        event.update(status="error", error_type="invalid_provider_json")
                    try:
                        handler.wfile.write(raw)
                    except OSError:
                        disconnected = True
                if disconnected:
                    event.update(status="error", error_type="client_disconnect")
        except (OSError, ValueError, urllib.error.URLError) as error:
            event.update(status="error", error_type=type(error).__name__)
            if not sent:
                try:
                    handler.reply(502, {"error": "upstream request failed"})
                except OSError:
                    pass
        finally:
            self.finish(event)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        # Let in-flight requests finish accounting even after the agent exits.
        deadline = time.monotonic() + self.timeout + 1
        while self.pending and time.monotonic() < deadline:
            time.sleep(0.01)
        with self.lock:
            self.closed = True
            for event, _, _ in list(self.pending.values()):
                self.finish(event, status="error", error_type="service_shutdown")
            self.store.add(
                {
                    "run_id": self.store.run_id,
                    "event_id": str(uuid.uuid4()),
                    "kind": "finish",
                    "coverage": ["model", "tool", "step"],
                    "source": "supervisor",
                }
            )

    def search_metrics(self):
        return {
            "search_pages_fetched": sorted(self.pages),
            "all_search_pages_fetched": self.pages == {1, 2, 3},
            "search_total_pages": 3,
        }
