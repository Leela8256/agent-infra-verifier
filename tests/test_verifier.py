import hashlib
import json
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import psutil

from infra_verifier.cli import result_exit_code
from infra_verifier.config import Config
from infra_verifier.quality import DEFAULT_VERIFIER
from infra_verifier.runner import run as supervisor_run
from infra_verifier.runner import write_json
from infra_verifier.summary import summarize
from infra_verifier.telemetry import EventStore, Receiver

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/fixture_agent.py"
REFERENCE = "2026-11-15T00:00:00Z"


def run(*args, **kwargs):
    """Original suite deliberately exercises the retained legacy adapter mode."""
    return supervisor_run(*args, telemetry_source="adapter_reported", **kwargs)


class ConfigTests(unittest.TestCase):
    def test_invalid_limits_rejected(self):
        for kwargs in (
            {"wall_clock_s": 0},
            {"memory_mb": -1},
            {"cpu_time_s": float("nan")},
            {"cost_usd": float("inf")},
            {"steps": True},
            {"model_calls": 1.5},
            {"wall_clock_s": None},
            {"require_telemetry": "yes"},
            {"sample_interval_s": 0.001},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Config(**kwargs)

    def test_unknown_fields_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text('{"memroy_mb": 1024}')
            with self.assertRaisesRegex(ValueError, "unknown"):
                Config.load(p)

    def test_contract_hash_changes(self):
        self.assertEqual(Config().digest, Config().digest)
        self.assertNotEqual(Config().digest, Config(memory_mb=100).digest)


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "events.jsonl", "test-run")
        self.store.started = time.monotonic()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def event(self, eid="m1", **overrides):
        return {
            "run_id": "test-run",
            "event_id": eid,
            "kind": "model",
            "name": "test-model",
            "status": "ok",
            "duration_s": 0.1,
            "start_offset_s": 0,
            "tokens_in": 100,
            "tokens_out": 10,
            "cost_usd": 0.001,
            **overrides,
        }

    def finish(self):
        self.store.add(
            {
                "run_id": "test-run",
                "event_id": "end",
                "kind": "finish",
                "coverage": ["model", "tool", "step"],
            }
        )

    def test_duplicate_transport_event_not_double_counted(self):
        event = self.event()
        self.store.add(event)
        self.store.add(event)
        self.finish()
        self.assertEqual(self.store.metrics()["tokens_in"], 100)
        self.assertEqual(self.store.metrics()["llm_calls"], 1)

    def test_conflicting_duplicates_rejected(self):
        self.store.add(self.event())
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.store.add(self.event(tokens_in=200))

    def test_missing_cost_not_zero(self):
        self.store.add(self.event(cost_usd=None))
        self.finish()
        self.assertIsNone(self.store.metrics()["cost_usd"])
        self.assertIn("cost_usd", self.store.completeness())

    def test_partial_stream_totals_are_unknown_but_budget_still_enforced(self):
        self.store.add(self.event(tokens_in=900))
        self.assertIsNone(self.store.metrics()["tokens_in"])
        self.assertIn("tokens_in", self.store.budget_hits(Config(tokens_in=100)))

    def test_zero_usage_only_with_finish_and_coverage(self):
        self.assertIsNone(self.store.metrics()["llm_calls"])
        self.finish()
        self.assertEqual(self.store.metrics()["llm_calls"], 0)
        self.assertEqual(self.store.metrics()["cost_usd"], 0)
        self.assertEqual(self.store.completeness(), [])

    def test_invalid_values(self):
        for event in (
            self.event(tokens_in=-1),
            self.event(tokens_out=1.2),
            self.event(cost_usd=float("nan")),
            self.event(duration_s=True),
            self.event(run_id="wrong"),
            self.event(kind="unknown"),
            self.event(retry_of="absent"),
        ):
            with self.subTest(event=event), self.assertRaises(ValueError):
                self.store.add(event)

    def test_retry_count_and_recovery(self):
        self.store.add(
            self.event(
                "t1", kind="tool", name="search", status="error", error_type="http_429"
            )
        )
        self.store.add(self.event("t2", kind="tool", name="search", retry_of="t1"))
        self.finish()
        m = self.store.metrics()
        self.assertEqual(m["retries"], 1)
        self.assertEqual(m["errors_by_type"], {"http_429": 1})
        self.assertTrue(m["retry_recovered"])

    def test_events_after_finish_rejected(self):
        self.finish()
        with self.assertRaisesRegex(ValueError, "after finish"):
            self.store.add(self.event())

    def test_http_auth_and_invalid_payload(self):
        receiver = Receiver(self.store)
        receiver.start()
        try:
            url = receiver.environment["INFRA_EVENT_URL"]
            req = urllib.request.Request(url, data=b"{}")
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req)
            self.assertEqual(error.exception.code, 401)
            error.exception.close()
            req = urllib.request.Request(
                url,
                data=b"not-json",
                headers={"Authorization": "Bearer " + receiver.token},
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            self.assertTrue(self.store.errors)
            self.assertEqual(len(self.store.events), 0)
        finally:
            receiver.close()


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.index = 0

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, mode, config=None, quality=True):
        self.index += 1
        directory = self.base / f"run{self.index}"
        options = (
            {
                "feed": ROOT / "examples/feed.json",
                "banned": ROOT / "examples/banned_words.txt",
                "reference_time": REFERENCE,
            }
            if quality
            else {}
        )
        result = run(
            [sys.executable, str(FIXTURE), mode],
            directory,
            config or Config(wall_clock_s=5),
            framework="fixture",
            **options,
        )
        return result, directory

    def test_success_measures_real_process_and_preserves_quality(self):
        report, directory = self.fixture("success")
        self.assertEqual(report["runtime_status"], "completed")
        self.assertEqual(report["measurement_status"], "complete")
        self.assertEqual(report["quality"]["verdict"], "pass")
        self.assertGreater(report["metrics"]["wall_clock_s"], 0)
        self.assertGreater(report["metrics"]["cpu_time_s"], 0)
        self.assertGreater(report["metrics"]["reaped_peak_rss_mb"], 0)
        self.assertEqual(report["metrics"]["tokens_in"], 1200)
        self.assertEqual(report["metrics"]["tool_calls"], 3)
        self.assertEqual(result_exit_code(report), 0)
        self.assertTrue((directory / "manifest.json").exists())
        self.assertTrue((directory / "samples.jsonl").exists())
        self.assertEqual(
            json.loads((directory / "quality.json").read_text()), report["quality"]
        )

    def test_missing_output_keeps_quality_failure(self):
        report, _ = self.fixture("missing-output")
        self.assertEqual(report["runtime_status"], "completed")
        self.assertEqual(report["measurement_status"], "complete")
        self.assertEqual(report["quality"]["verdict"], "fail")
        self.assertEqual(result_exit_code(report), 2)

    def test_timeout_is_stopped(self):
        report, _ = self.fixture("timeout", Config(wall_clock_s=0.35))
        self.assertEqual(report["runtime_status"], "budget_exceeded")
        self.assertIn("wall_clock_s", report["budget_stop_triggers"])
        self.assertLess(report["metrics"]["wall_clock_s"], 4)
        self.assertTrue(report["metrics"]["hit_budget_cap"])

    def test_memory_budget(self):
        report, _ = self.fixture("memory", Config(wall_clock_s=5, memory_mb=40))
        self.assertIn("memory_mb", report["budget_stop_triggers"])
        self.assertGreater(report["metrics"]["peak_rss_mb"], 40)

    def test_child_memory_and_cleanup(self):
        report, directory = self.fixture(
            "child-memory", Config(wall_clock_s=5, memory_mb=50)
        )
        self.assertIn("memory_mb", report["budget_stop_triggers"])
        pid = int((directory / "output/child.pid").read_text())
        self.assertFalse(
            psutil.pid_exists(pid)
            and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        )

    def test_cpu_budget(self):
        report, _ = self.fixture("cpu", Config(wall_clock_s=5, cpu_time_s=0.15))
        self.assertIn("cpu_time_s", report["budget_stop_triggers"])

    def test_token_budget(self):
        report, _ = self.fixture("token-budget", Config(wall_clock_s=5, tokens_in=1500))
        self.assertIn("tokens_in", report["budget_stop_triggers"])
        self.assertIsNone(report["metrics"]["tokens_in"])
        self.assertEqual(report["metrics"]["usage_known_subtotal"]["tokens_in"], 9000)

    def test_orphan_cleanup(self):
        report, directory = self.fixture("orphan")
        self.assertEqual(report["runtime_status"], "orphaned_processes")
        self.assertGreaterEqual(report["metrics"]["orphaned_processes"], 1)
        pid = int((directory / "output/child.pid").read_text())
        self.assertFalse(
            psutil.pid_exists(pid)
            and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        )

    def test_crash_not_misclassified_as_verifier_error(self):
        report, _ = self.fixture("crash")
        self.assertEqual(report["runtime_status"], "agent_error")
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["measurement_errors"], [])

    def test_missing_telemetry_is_unknown(self):
        report, _ = self.fixture("missing-telemetry")
        self.assertEqual(report["quality"]["verdict"], "pass")
        self.assertEqual(report["measurement_status"], "incomplete")
        self.assertEqual(report["budget_status"], "unknown")
        self.assertIsNone(report["metrics"]["cost_usd"])
        self.assertEqual(result_exit_code(report), 3)

    def test_unknown_usage(self):
        report, _ = self.fixture("unknown-usage")
        self.assertEqual(report["measurement_status"], "incomplete")
        self.assertIsNone(report["metrics"]["cost_usd"])
        self.assertEqual(report["quality"]["verdict"], "pass")

    def test_invalid_event(self):
        report, _ = self.fixture("malformed-event")
        self.assertEqual(report["measurement_status"], "invalid")
        self.assertEqual(result_exit_code(report), 3)

    def test_retry(self):
        report, _ = self.fixture("retry")
        self.assertEqual(report["metrics"]["tool_calls"], 4)
        self.assertEqual(report["metrics"]["retries"], 1)
        self.assertTrue(report["metrics"]["retry_recovered"])
        self.assertEqual(report["quality"]["verdict"], "pass")

    def test_launch_error_still_writes_report(self):
        report = run(["/does/not/exist"], self.base / "launch", Config())
        self.assertEqual(report["runtime_status"], "launch_error")
        self.assertEqual(report["measurement_status"], "invalid")
        self.assertEqual(result_exit_code(report), 3)

    def test_refuse_overwrite(self):
        _, directory = self.fixture("success")
        with self.assertRaises(FileExistsError):
            run([sys.executable, "-c", "pass"], directory, Config())

    def test_quality_exception_is_unscored(self):
        broken = self.base / "broken.py"
        broken.write_text("raise RuntimeError('bad verifier')")
        report = run(
            [sys.executable, str(FIXTURE), "success"],
            self.base / "quality_error",
            Config(),
            feed=ROOT / "examples/feed.json",
            banned=ROOT / "examples/banned_words.txt",
            reference_time=REFERENCE,
            verifier=broken,
        )
        self.assertEqual(report["quality"]["verdict"], "unscored")
        self.assertEqual(report["runtime_status"], "completed")
        self.assertEqual(result_exit_code(report), 3)

    def test_quality_inputs_required_before_launch(self):
        with self.assertRaises(ValueError):
            run(
                [sys.executable, "-c", "pass"],
                self.base / "bad",
                Config(),
                feed=ROOT / "examples/feed.json",
            )
        self.assertFalse((self.base / "bad").exists())

    def test_upstream_copy_has_original_git_blob_hash(self):
        content = DEFAULT_VERIFIER.read_bytes()
        blob = b"blob " + str(len(content)).encode() + b"\0" + content
        self.assertEqual(
            hashlib.sha1(blob).hexdigest(), "8be85f3ebacef524d919cad4c73c84575bfc41ee"
        )

    def test_model_mismatch_is_invalid(self):
        experiment = self.base / "experiment.json"
        experiment.write_text(
            json.dumps(
                {
                    "model": "different-model",
                    "provider": "fixture",
                    "temperature": 0,
                    "max_tokens": 1000,
                    "cache_policy": "none",
                }
            )
        )
        report = run(
            [sys.executable, str(FIXTURE), "success"],
            self.base / "mismatch",
            Config(),
            experiment=experiment,
        )
        self.assertEqual(report["measurement_status"], "invalid")
        self.assertIn("observed model", report["measurement_errors"][0])

    def test_model_configuration_changes_comparison_group(self):
        keys = []
        for i, temperature in enumerate([0, 0.5]):
            path = self.base / f"experiment{i}.json"
            path.write_text(
                json.dumps(
                    {
                        "model": "synthetic-no-provider",
                        "provider": "fixture",
                        "temperature": temperature,
                        "max_tokens": 1000,
                        "cache_policy": "none",
                    }
                )
            )
            report = run(
                [sys.executable, str(FIXTURE), "success"],
                self.base / f"comparison{i}",
                Config(),
                experiment=path,
            )
            keys.append(report["comparison_key"])
        self.assertNotEqual(*keys)


class SummaryTests(unittest.TestCase):
    def report(self, id, cost, quality="pass", measurement="complete", key="same"):
        return {
            "run_id": id,
            "framework": "fixture",
            "trial": 1,
            "comparison_key": key,
            "runtime_status": "completed",
            "measurement_status": measurement,
            "budget_status": "within_observed_limits",
            "quality": {"verdict": quality, "checks": []},
            "budget_violations": [],
            "metrics": {
                "cost_usd": cost,
                "tokens_in": 100,
                "tokens_out": 10,
                "wall_clock_s": 1,
            },
        }

    def summarize(self, reports):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i, report in enumerate(reports):
                p = Path(d) / f"{i}.json"
                write_json(p, report)
                paths.append(p)
            return summarize(paths)

    def test_failed_attempt_cost_included(self):
        data = self.summarize(
            [
                self.report("a", 1),
                self.report("b", 2, quality="fail"),
                self.report("c", 3, quality="fail"),
            ]
        )
        self.assertEqual(data["aggregates"][0]["cost_per_passing_run"], 6)

    def test_no_success_is_null(self):
        data = self.summarize([self.report("a", 1, quality="fail")])
        self.assertIsNone(data["aggregates"][0]["cost_per_passing_run"])

    def test_missing_cost_does_not_produce_cheaper_result(self):
        data = self.summarize(
            [
                self.report("a", 1),
                self.report("b", None, quality="fail", measurement="incomplete"),
            ]
        )
        self.assertIsNone(data["aggregates"][0]["total_cost_usd"])
        self.assertIsNone(data["aggregates"][0]["cost_per_passing_run"])

    def test_contracts_not_pooled(self):
        data = self.summarize(
            [self.report("a", 1), self.report("b", 2, key="different")]
        )
        self.assertEqual(data["comparison_groups"], 2)
        self.assertEqual(len(data["aggregates"]), 2)

    def test_duplicate_runs_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.summarize([self.report("a", 1), self.report("a", 1)])


if __name__ == "__main__":
    unittest.main()
