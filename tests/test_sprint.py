import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.mock_langfuse import FakeLangfuse
from examples.mock_provider import MockProvider
from infra_verifier.cli import result_exit_code
from infra_verifier.config import Config
from infra_verifier.langfuse_export import export, payloads
from infra_verifier.protocol import freeze, inspect_spec, run_trial
from infra_verifier.runner import run
from infra_verifier.sandbox import command, probe

ROOT = Path(__file__).resolve().parents[1]


class SprintTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(
        self,
        mode="success",
        *,
        trace_mode="ok",
        guard=False,
        config=None,
        phase_evidence="declared_or_observed",
    ):
        directory = self.root / "run"
        with (
            MockProvider() as provider,
            FakeLangfuse(trace_mode) as tracing,
            patch.dict(os.environ, tracing.env),
        ):
            report = run(
                [
                    sys.executable,
                    str(ROOT / "examples/phase_agent.py"),
                    mode,
                    str(self.root / "outside.txt"),
                ],
                directory,
                config or Config(wall_clock_s=5),
                model_upstream=provider.url,
                feed=ROOT / "examples/feed.json",
                banned=ROOT / "examples/banned_words.txt",
                reference_time="2026-11-15T00:00:00Z",
                experiment=ROOT / "examples/experiment.json",
                require_phases=True,
                langfuse="required",
                filesystem_mode="enforce" if guard else "audit",
                enforce_model_settings=True,
                phase_evidence=phase_evidence,
            )
        return report, directory, tracing

    def test_phase_evidence_and_langfuse_export(self):
        report, directory, tracing = self.fixture()
        self.assertEqual(report["workflow"]["status"], "evidenced")
        self.assertEqual(report["metrics"]["workflow_phase_count"], 3)
        self.assertEqual(report["langfuse_export"]["status"], "accepted")
        self.assertEqual(result_exit_code(report), 0)
        self.assertIn("/project/fixture-project/traces/", report["trace_url"])
        spans = [
            s
            for b in tracing.batches
            for r in b["resourceSpans"]
            for scope in r["scopeSpans"]
            for s in scope["spans"]
        ]
        self.assertEqual(
            len(spans), 9
        )  # root + 1 generation + 4 search attempts + 3 phases
        for span in spans:
            self.assertEqual(len(span["traceId"]), 32)
            self.assertEqual(len(span["spanId"]), 16)
        self.assertEqual(tracing.headers[0]["x-langfuse-ingestion-version"], "4")
        self.assertNotIn(
            "fixture-secret", (directory / "langfuse-otlp.json").read_text()
        )
        self.assertTrue((directory / "phase-evidence/001-draft.json").is_file())

    def test_missing_phases_never_claim_workflow_completion(self):
        # Strict mode: explicit checkpoints are required.
        report, _, _ = self.fixture("missing", phase_evidence="declared")
        self.assertEqual(report["quality"]["verdict"], "pass")
        self.assertEqual(report["workflow"]["status"], "missing_evidence")
        self.assertEqual(result_exit_code(report), 3)

    def test_report_names_the_checker_that_graded_it(self):
        report, _, _ = self.fixture()
        self.assertTrue(report["quality_checker"]["is_upstream"])

    def test_default_records_a_single_saved_version_as_a_soft_finding(self):
        # Default mode: no checkpoints, one saved blog.md -> no visible revision.
        report, _, _ = self.fixture("missing")
        self.assertEqual(report["workflow"]["status"], "single_version_observed")
        check = {c["id"]: c for c in report["infra_checks"]}["draft_check_fix_evidence"]
        self.assertEqual((check["result"], check["hard"]), ("fail", False))
        self.assertNotEqual(result_exit_code(report), 3)

    def test_wrong_order_is_detected_after_run(self):
        report, _, _ = self.fixture("wrong-order")
        self.assertEqual(report["runtime_status"], "completed")
        self.assertEqual(report["workflow"]["status"], "inconsistent")
        self.assertEqual(result_exit_code(report), 2)

    def test_unfixed_findings_are_visible(self):
        report, _, _ = self.fixture("unfixed")
        self.assertEqual(report["workflow"]["status"], "inconsistent")

    def test_native_guard_in_real_agent_run(self):
        report, _, _ = self.fixture("outside-write", guard=True)
        self.assertEqual(report["runtime_status"], "completed")
        self.assertEqual(report["write_guard"]["probe"], "passed")
        self.assertFalse((self.root / "outside.txt").exists())
        # v2: a blocked attempt is now a hard finding instead of a silent pass.
        blocked = {c["id"]: c for c in report["infra_checks"]}["no_blocked_writes"]
        if blocked["result"] == "unscored":  # host cannot observe guard denials
            self.assertEqual(result_exit_code(report), 0)
        else:
            self.assertEqual(blocked["result"], "fail")
            self.assertTrue(any("outside.txt" in d["path"] for d in blocked["detail"]))
            self.assertEqual(
                report["metrics"]["writes_blocked_by_guard"], len(blocked["detail"])
            )
            self.assertEqual(result_exit_code(report), 2)

    def test_trace_partial_ingestion_blocks_success(self):
        report, _, _ = self.fixture(trace_mode="partial")
        self.assertEqual(report["langfuse_export"]["status"], "failed")
        self.assertEqual(report["quality"]["verdict"], "pass")
        self.assertEqual(result_exit_code(report), 3)

    def test_trace_export_replay_preserves_ids_and_does_not_rerun_agent(self):
        report, directory, _ = self.fixture(trace_mode="auth")
        before_id, before_payloads = payloads(directory)
        with FakeLangfuse() as tracing, patch.dict(os.environ, tracing.env):
            result = export(directory)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(before_id, result["trace_id"])
        self.assertEqual(before_payloads, tracing.batches)
        self.assertEqual(report["metrics"]["llm_calls"], 1)

    def test_record_only_budgets_do_not_stop_agent(self):
        report, _, _ = self.fixture(
            config=Config(wall_clock_s=5, tokens_in=1, enforce_budgets=False)
        )
        self.assertEqual(report["runtime_status"], "completed")
        self.assertEqual(report["budget_status"], "exceeded")
        self.assertFalse(report["metrics"]["hit_budget_cap"])

    def test_native_guard_blocks_absolute_symlink_truncate_and_child_writes(self):
        output = self.root / "output"
        output.mkdir()
        protected = self.root / "protected.txt"
        protected.write_text("unchanged")
        (output / "escape").symlink_to(protected)
        probe(output)
        code = """import os,pathlib,subprocess,sys
for path in sys.argv[1:]:
    try: pathlib.Path(path).write_text('overwrite')
    except PermissionError: pass
    else: raise AssertionError('unguarded write')
child=subprocess.run([sys.executable,'-c',"import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('child')",sys.argv[1]],capture_output=True)
assert child.returncode != 0
"""
        result = subprocess.run(
            command(
                [sys.executable, "-c", code, str(protected), str(output / "escape")],
                output,
            ),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(protected.read_text(), "unchanged")

    def spec(self, provider, tracing):
        data = json.loads((ROOT / "experiment/sprint.template.json").read_text())
        data.update(
            status="ready",
            sprint_id="test-sprint",
            framework_owners={"fixture": "test-owner"},
            model="synthetic-no-provider",
            provider="offline-fixture",
            model_upstream=provider.url,
            temperature=0,
            max_tokens=8000,
            cache_policy="disabled",
            request_timeout_s=10,
            sdk_retry_policy="one retry of the planted 429",
            reference_time="2026-11-15T00:00:00Z",
            budget_policy="record_and_stop",
            execution_host={
                "system": platform.system(),
                "machine": platform.machine(),
                "logical_cpus": os.cpu_count(),
            },
            langfuse_base_url=tracing.env["LANGFUSE_BASE_URL"],
            langfuse_project_id="fixture-project",
            quality_review_protocol="Mithilesh reviews model rubric after run",
            feed_record_count=6,
            paths={
                "prompt": str(ROOT / "experiment/task.prompt.txt"),
                "feed": str(ROOT / "examples/feed.json"),
                "banned": str(ROOT / "examples/banned_words.txt"),
            },
        )
        data["budgets"] = {
            "wall_clock_s": 5,
            "cpu_time_s": "disabled",
            "memory_mb": "disabled",
            "model_calls": 20,
            "tool_calls": 30,
            "tokens_in": 50000,
            "tokens_out": 8000,
            "cost_usd": 1,
            "steps": 50,
        }
        path = self.root / "spec.json"
        path.write_text(json.dumps(data))
        return path

    def test_preflight_detects_missing_old_and_banned_traps(self):
        with MockProvider() as provider, FakeLangfuse() as tracing:
            spec_path = self.spec(provider, tracing)
            spec = json.loads(spec_path.read_text())
            feed = json.loads((ROOT / "examples/feed.json").read_text())
            feed[4]["published_at"] = "2026-11-14T00:00:00Z"
            feed[4].pop("summary")
            feed_path = self.root / "feed.json"
            feed_path.write_text(json.dumps(feed))
            spec["paths"]["feed"] = str(feed_path)
            spec_path.write_text(json.dumps(spec))
            _, result, _, _ = inspect_spec(spec_path)
        reasons = [x["reason"] for x in result["blockers"]]
        self.assertIn("the old high-score trap is missing", reasons)
        self.assertIn("the banned-word trap is missing", reasons)

    def test_metadata_fix_and_additional_review_cycle_are_accepted(self):
        from infra_verifier.phases import PhaseEvidence
        from infra_verifier.runner import sha256
        from infra_verifier.telemetry import EventStore

        output = self.root / "output"
        output.mkdir()
        (output / "blog.md").write_text("draft")
        (output / "meta.json").write_text("{}")
        store = EventStore(self.root / "events.jsonl", "phase-test")
        self.addCleanup(store.stream.close)
        store.started = time.monotonic()
        phases = PhaseEvidence(store, output, self.root / "phases")
        phases.capture({"phase": "draft"})
        phases.capture({"phase": "check", "findings": ["metadata missing"]})
        (output / "meta.json").write_text('{"topics": []}')
        phases.capture({"phase": "fix", "resolution": "added topics"})
        phases.capture({"phase": "check", "findings": []})
        phases.capture({"phase": "fix", "resolution": "no further issues"})
        hashes = {name: sha256(output / name) for name in ("blog.md", "meta.json")}
        self.assertEqual(phases.result(hashes)["status"], "evidenced")
        (output / "meta.json").write_text("{}")
        hashes["meta.json"] = sha256(output / "meta.json")
        self.assertEqual(phases.result(hashes)["status"], "inconsistent")

    def test_template_is_explicitly_not_ready(self):
        _, result, _, _ = inspect_spec(ROOT / "experiment/sprint.template.json")
        self.assertFalse(result["ready"])
        self.assertIn("model", {b["field"] for b in result["blockers"]})

    def test_freeze_and_sprint_trial_prevent_overwrite(self):
        with (
            MockProvider() as provider,
            FakeLangfuse() as tracing,
            patch.dict(os.environ, tracing.env),
        ):
            spec = self.spec(provider, tracing)
            bundle = self.root / "bundle"
            freeze(spec, bundle)
            self.assertNotIn("{{BANNED_WORDS}}", (bundle / "prompt.txt").read_text())
            argv = [sys.executable, str(ROOT / "examples/phase_agent.py")]
            report = run_trial(bundle, "fixture", 1, self.root / "runs", argv)
            self.assertEqual(report["sprint_id"], "test-sprint")
            self.assertEqual(result_exit_code(report), 0)
            with self.assertRaises(FileExistsError):
                run_trial(bundle, "fixture", 1, self.root / "runs", argv)

    def test_freeze_keeps_banned_word_comments_out_of_prompt(self):
        with MockProvider() as provider, FakeLangfuse() as tracing:
            spec_path = self.spec(provider, tracing)
            banned = self.root / "banned_with_comments.txt"
            banned.write_text("# Frozen with prompt.md\nsynergy\n  # indented note\ndelve\n")
            data = json.loads(spec_path.read_text())
            data["paths"]["banned"] = str(banned)
            spec_path.write_text(json.dumps(data))
            bundle = self.root / "bundle"
            freeze(spec_path, bundle)
            prompt = (bundle / "prompt.txt").read_text()
            self.assertIn("synergy, delve", prompt)
            self.assertNotIn("#", prompt.split("these words:")[1].split(".")[0])

    def test_agent_receives_reference_time(self):
        with (
            MockProvider() as provider,
            FakeLangfuse() as tracing,
            patch.dict(os.environ, tracing.env),
        ):
            bundle = self.root / "bundle"
            freeze(self.spec(provider, tracing), bundle)
            script = self.root / "print_ref.py"
            script.write_text("import os; print('REF=' + os.environ.get('INFRA_REFERENCE_TIME', 'missing'))")
            run_trial(bundle, "fixture", 1, self.root / "runs", [sys.executable, str(script)])
            logs = list((self.root / "runs").rglob("stdout.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("REF=2026-11-15T00:00:00Z", logs[0].read_text())

    def test_tampered_bundle_rejected_before_agent_runs(self):
        with MockProvider() as provider, FakeLangfuse() as tracing:
            bundle = self.root / "bundle"
            freeze(self.spec(provider, tracing), bundle)
            (bundle / "feed.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "changed"):
                run_trial(bundle, "fixture", 1, self.root / "runs", ["false"])

    def test_model_settings_mismatch_not_forwarded(self):
        script = self.root / "wrong.py"
        script.write_text("""import os,json,urllib.request,urllib.error
for body in ({'model':'wrong','messages':[]}, {'model':'synthetic-no-provider','max_tokens':8000,'max_completion_tokens':9000,'messages':[]}):
 req=urllib.request.Request(os.environ['INFRA_MODEL_BASE_URL']+'/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+os.environ['INFRA_PROXY_KEY']})
 try: urllib.request.urlopen(req)
 except urllib.error.HTTPError as e: assert e.code==409; e.close()
""")
        with MockProvider() as provider:
            report = run(
                [sys.executable, str(script)],
                self.root / "mismatch",
                Config(),
                search_feed=ROOT / "examples/feed.json",
                model_upstream=provider.url,
                experiment=ROOT / "examples/experiment.json",
                enforce_model_settings=True,
            )
        self.assertEqual(provider.requests, [])
        self.assertEqual(report["measurement_status"], "invalid")


if __name__ == "__main__":
    unittest.main()
