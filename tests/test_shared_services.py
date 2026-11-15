import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from examples.mock_provider import MockProvider
from infra_verifier.cli import result_exit_code
from infra_verifier.config import Config
from infra_verifier.findings import failure_tags
from infra_verifier.http_server import LoopbackHTTPServer
from infra_verifier.runner import run
from infra_verifier.services import SharedServices, validate_upstream
from infra_verifier.summary import summarize, write_csv
from infra_verifier.telemetry import EventStore
from infra_verifier.workspace import audit, snapshot

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "examples/feed.json"


class SharedRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, mode="success", provider_mode="normal", config=None):
        with (
            MockProvider(provider_mode) as provider,
            patch.dict(os.environ, {"TEST_PROVIDER_SECRET": "fixture-secret"}),
        ):
            report = run(
                [sys.executable, str(ROOT / "examples/shared_agent.py"), mode],
                self.base / "run",
                config or Config(wall_clock_s=5),
                model_upstream=provider.url,
                model_key_env="TEST_PROVIDER_SECRET",
                feed=FEED,
                banned=ROOT / "examples/banned_words.txt",
                reference_time="2026-11-15T00:00:00Z",
            )
        return report, provider.requests

    def test_shared_accounting_without_recorder_and_secret_withheld(self):
        report, requests = self.fixture()
        self.assertEqual(report["measurement_status"], "complete")
        self.assertEqual(report["quality"]["verdict"], "pass")
        self.assertEqual(report["usage_source"], "provider_usage_via_proxy")
        metrics = report["metrics"]
        self.assertEqual(
            (metrics["tokens_in"], metrics["tokens_out"], metrics["cost_usd"]),
            (1200, 700, 0.004),
        )
        self.assertEqual(
            (metrics["llm_calls"], metrics["tool_calls"], metrics["steps"]), (1, 4, 1)
        )
        self.assertEqual(
            (metrics["errors"], metrics["retries"], metrics["retry_recovered"]),
            (1, 1, True),
        )
        self.assertEqual(metrics["search_pages_fetched"], [1, 2, 3])
        self.assertEqual(requests[0]["run_id"], report["run_id"])
        self.assertEqual(requests[0]["authorization"], "Bearer fixture-secret")
        self.assertEqual(result_exit_code(report), 0)
        for path in (self.base / "run").glob("*.json*"):
            self.assertNotIn("fixture-secret", path.read_text())

    def test_streaming_usage_not_double_counted(self):
        report, _ = self.fixture("stream")
        self.assertEqual(report["metrics"]["tokens_in"], 1200)
        self.assertEqual(report["metrics"]["llm_calls"], 1)
        self.assertEqual(report["measurement_status"], "complete")

    @unittest.skipUnless(
        shutil.which("curl"), "curl is required for the non-Python client test"
    )
    def test_cli_with_shell_curl_client_no_recorder(self):
        script = """set -eu
for page in 1 1 2 3; do
  curl -sS "$INFRA_SEARCH_URL?page=$page" -H "Authorization: Bearer $INFRA_PROXY_KEY" >/dev/null
done
curl -fsS "$INFRA_MODEL_BASE_URL/chat/completions" -H "Authorization: Bearer $INFRA_PROXY_KEY" -H 'Content-Type: application/json' -d '{"model":"synthetic-no-provider","messages":[]}' >/dev/null
"""
        with MockProvider() as provider:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "infra_verifier",
                    "run",
                    "--framework",
                    "shell-curl",
                    "--run-dir",
                    str(self.base / "cli"),
                    "--search-feed",
                    str(FEED),
                    "--model-upstream",
                    provider.url,
                    "--",
                    "/bin/sh",
                    "-c",
                    script,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((self.base / "cli/report.json").read_text())
        self.assertEqual(report["metrics"]["tokens_in"], 1200)
        self.assertEqual(report["metrics"]["tool_calls"], 4)

    def test_responses_nonstream_usage(self):
        report, _ = self.fixture("responses")
        self.assertEqual(report["metrics"]["tokens_out"], 700)

    def test_responses_stream_usage(self):
        report, _ = self.fixture("responses-stream")
        self.assertEqual(report["metrics"]["cost_usd"], 0.004)

    def test_missing_usage_remains_unknown(self):
        report, _ = self.fixture(provider_mode="missing-cost")
        self.assertIsNone(report["metrics"]["cost_usd"])
        self.assertEqual(report["measurement_status"], "incomplete")

    def test_invalid_provider_usage_invalidates_measurement(self):
        report, _ = self.fixture(provider_mode="invalid-usage")
        self.assertEqual(report["measurement_status"], "invalid")

    def test_truncated_stream_records_error(self):
        report, _ = self.fixture("stream", "truncated")
        self.assertEqual(report["metrics"]["errors_by_type"]["truncated_stream"], 1)

    def test_stream_error_inside_http_200_is_recorded(self):
        report, _ = self.fixture("stream", "stream-error")
        self.assertEqual(report["metrics"]["errors_by_type"]["provider_error"], 1)

    def test_missing_page_fails_infra_without_rewriting_quality(self):
        report, _ = self.fixture("missing-page")
        self.assertEqual(report["quality"]["verdict"], "pass")
        self.assertFalse(report["metrics"]["all_search_pages_fetched"])
        self.assertIn("looped_or_quit_early", report["failure_tags"])
        self.assertEqual(result_exit_code(report), 2)

    def test_persistent_write_outside_output_is_reported(self):
        report, _ = self.fixture("outside-write")
        self.assertEqual(report["metrics"]["writes_outside_output"], 1)
        self.assertEqual(
            report["workspace_audit"]["changes_outside_output"],
            [{"path": "leaked.txt", "change": "created"}],
        )
        self.assertIn("file_or_state_error", report["failure_tags"])
        self.assertEqual(result_exit_code(report), 2)

    def test_zero_exit_quality_fail_is_false_done(self):
        report, _ = self.fixture("false-done")
        self.assertEqual(report["exit_code"], 0)
        self.assertTrue(report["metrics"]["said_done_but_failed"])
        self.assertIn("said_done_but_failed", report["failure_tags"])

    def test_proxy_tokens_feed_watchdog(self):
        report, _ = self.fixture(
            "token-budget", config=Config(wall_clock_s=5, tokens_in=1000)
        )
        self.assertEqual(report["runtime_status"], "budget_exceeded")
        self.assertIn("tokens_in", report["budget_stop_triggers"])

    def test_csv_contains_sync_fields_and_separate_warnings(self):
        report, _ = self.fixture()
        path = self.base / "run/report.json"
        report["quality"]["checks"].append(
            {"result": "fail", "hard": False, "tag": "lost_context"}
        )
        path.write_text(json.dumps(report))
        summary = summarize([path])
        csv_path = self.base / "summary.csv"
        write_csv(csv_path, summary)
        with csv_path.open() as source:
            row = next(csv.DictReader(source))
        for field in (
            "steps",
            "errors",
            "retry_recovered",
            "hit_budget_cap",
            "time_to_first_tool_call_s",
            "orphaned_processes",
        ):
            self.assertNotEqual(row[field], "")
        self.assertEqual(row["warning_tags"], "lost_context")
        self.assertNotIn("lost_context", row["failure_tags"])


class ServiceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.temp.name) / "events.jsonl", "test-run")
        self.store.started = time.monotonic()
        self.provider = MockProvider("retry")
        self.provider.__enter__()
        self.service = SharedServices(
            self.store, json.loads(FEED.read_text()), self.provider.url, "secret"
        )
        self.service.start()

    def tearDown(self):
        self.service.close()
        self.store.close()
        self.provider.__exit__()
        self.temp.cleanup()

    def request(self, path, data=None, token=None, run_id="test-run"):
        req = urllib.request.Request(
            self.service.environment["INFRA_MODEL_BASE_URL"].removesuffix("/v1") + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={
                "Authorization": "Bearer " + (token or self.service.token),
                "X-Infra-Run-ID": run_id,
            },
        )
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read())

    def test_wrong_token_and_run_are_rejected(self):
        for kwargs, status in [({"token": "wrong"}, 401), ({"run_id": "other"}, 403)]:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/search", **kwargs)
            self.assertEqual(caught.exception.code, status)
            caught.exception.close()
        self.assertEqual(len(self.store.events), 0)

    def test_model_retry_logged_at_proxy_with_unknown_error_cost(self):
        data = {"model": "test", "messages": []}
        original_finish = self.service.finish

        def slow_finish(*args, **kwargs):
            time.sleep(0.05)
            return original_finish(*args, **kwargs)

        # A retry issued immediately on HTTP error must still link to that error,
        # even when persisting its accounting takes longer than the client retry.
        with patch.object(self.service, "finish", side_effect=slow_finish):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/v1/chat/completions", data)
            caught.exception.close()
            self.request("/v1/chat/completions", data)
        # Shutdown drains the last handler before final totals.
        self.service.server.shutdown()
        deadline = time.monotonic() + 2
        while self.service.pending and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.store.metrics()["llm_calls"], 2)
        self.assertEqual(self.store.metrics()["retries"], 1)
        self.assertIsNone(self.store.metrics()["cost_usd"])

    def test_pagination_cannot_pass_by_repeating_one_page(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/search?page=1")
        caught.exception.close()
        for _ in range(3):
            self.request("/search", {"page": 1})
        self.assertEqual(self.service.search_metrics()["search_pages_fetched"], [1])
        self.assertFalse(self.service.search_metrics()["all_search_pages_fetched"])

    def test_proxy_cannot_accept_agent_usage_events(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/event", {"tokens_in": 999999})
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()
        self.assertEqual(len(self.store.events), 0)

    def test_each_run_has_independent_429_and_credential(self):
        second_store = EventStore(Path(self.temp.name) / "second.jsonl", "second-run")
        second_store.started = time.monotonic()
        second = SharedServices(
            second_store, json.loads(FEED.read_text()), self.provider.url, "secret"
        )
        second.start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/search", token=second.token)
            self.assertEqual(caught.exception.code, 401)
            caught.exception.close()
            for service in (self.service, second):
                request = urllib.request.Request(
                    service.environment["INFRA_SEARCH_URL"],
                    headers={"Authorization": "Bearer " + service.token},
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request)
                self.assertEqual(caught.exception.code, 429)
                caught.exception.close()
        finally:
            second.close()
            second_store.close()

    def test_upstream_redirect_not_followed(self):
        self.provider.mode = "redirect"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/v1/chat/completions", {"model": "test"})
        caught.exception.close()
        self.assertEqual(len(self.provider.requests), 1)


class AuditAndTagsTests(unittest.TestCase):
    def test_loopback_server_does_not_resolve_hostnames(self):
        from http.server import BaseHTTPRequestHandler

        with patch("socket.getfqdn", side_effect=AssertionError("reverse DNS")):
            server = LoopbackHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            self.assertEqual(server.server_name, "127.0.0.1")
            self.assertGreater(server.server_port, 0)
            server.server_close()

    def test_snapshot_detects_modify_delete_and_symlink_without_following(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "output").mkdir()
            (root / "changed").write_text("before")
            (root / "removed").write_text("before")
            before = snapshot(root)
            (root / "changed").write_text("after")
            (root / "removed").unlink()
            (root / "escape").symlink_to(root.parent)
            (root / "output/blog.md").write_text("allowed")
            changes = audit(before, snapshot(root))["changes_outside_output"]
            self.assertEqual(
                {c["change"] for c in changes}, {"created", "modified", "deleted"}
            )
            self.assertNotIn("output/blog.md", {c["path"] for c in changes})

    def test_partial_soft_tags_do_not_become_hard_failures(self):
        report = {
            "exit_code": 0,
            "quality": {
                "verdict": "partial",
                "checks": [{"tag": "lost_context", "hard": False, "result": "fail"}],
            },
        }
        self.assertEqual(failure_tags(report), ([], ["lost_context"]))
        report["exit_code"] = 1
        report["quality"]["verdict"] = "fail"
        self.assertNotIn("said_done_but_failed", failure_tags(report)[0])

    def test_upstream_url_validation(self):
        for value in (
            "http://remote.example/v1",
            "https://user:secret@example.test/v1",
            "https://example.test?key=secret",
        ):
            with self.assertRaises(ValueError):
                validate_upstream(value)

    def test_loopback_opener_does_not_discover_system_proxies(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(Path(directory) / "events.jsonl", "proxy-test")
            store.started = time.monotonic()
            with patch(
                "urllib.request.getproxies",
                side_effect=AssertionError("system proxy lookup"),
            ):
                service = SharedServices(store, [1, 2, 3], "http://127.0.0.1:1/v1", "")
                service.start()
                service.close()
            store.close()
