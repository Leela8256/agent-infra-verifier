"""Tests for the v2 improvements (Pydantic AI owner's proposal, not yet team-approved)."""

import json
import tempfile
import unittest
from pathlib import Path

from infra_verifier.protocol import inspect_spec
from infra_verifier.runner import observe_revisions

ROOT = Path(__file__).resolve().parents[1]


class FakeHandler:
    def __init__(self, path="/v1/chat/completions"):
        self.path = path
        self.replies = []

    def reply(self, status, data, headers=None):
        self.replies.append((status, data))


class FakeStore:
    errors = []

    def invalid(self, message):
        self.errors.append(message)


def services(settings):
    from infra_verifier.services import SharedServices

    s = SharedServices.__new__(SharedServices)
    s.model_settings = settings
    s.store = FakeStore()
    s.closed = False
    return s


class ProxyV2Tests(unittest.TestCase):
    def test_price_table_fills_missing_provider_cost(self):
        s = services({"price_per_mtok": {"input": 4, "output": 20}})
        event = {"cost_usd": None}
        s.usage(
            event, {"usage": {"prompt_tokens": 1_000_000, "completion_tokens": 100_000}}
        )
        self.assertEqual(event["cost_usd"], 6.0)
        self.assertEqual(event["cost_source"], "frozen_price_table")

    def test_provider_cost_wins_over_price_table(self):
        s = services({"price_per_mtok": {"input": 4, "output": 20}})
        event = {"cost_usd": None}
        s.usage(
            event,
            {"usage": {"prompt_tokens": 10, "completion_tokens": 10, "cost": 0.5}},
        )
        self.assertEqual(event["cost_usd"], 0.5)
        self.assertEqual(event["cost_source"], "provider_response_usage.cost")

    def test_omitted_temperature_rejects_an_explicit_one(self):
        s = services({"model": "m", "temperature": None, "max_tokens": 10})
        handler = FakeHandler()
        s.model(handler, {"model": "m", "temperature": 0, "messages": []})
        self.assertEqual(handler.replies[0][0], 409)
        self.assertIn("temperature", handler.replies[0][1]["fields"])

    def test_unfrozen_reasoning_effort_passes_through(self):
        s = services({"model": "m", "temperature": 0, "max_tokens": 10})
        handler = FakeHandler()
        try:
            s.model(handler, {"model": "m", "reasoning_effort": "low", "messages": []})
        except Exception:
            pass  # the fake has no upstream; only the conflict decision matters here
        self.assertFalse(any(code == 409 for code, _ in handler.replies))

    def test_frozen_reasoning_effort_rejects_a_different_value(self):
        settings = {
            "model": "m",
            "temperature": 0,
            "max_tokens": 10,
            "reasoning_effort": "high",
        }
        for path, body in (
            ("/v1/chat/completions", {"reasoning_effort": "low"}),
            ("/v1/responses", {"reasoning": {"effort": "low"}}),
        ):
            with self.subTest(path=path):
                handler = FakeHandler(path)
                services(settings).model(
                    handler, {"model": "m", "messages": [], **body}
                )
                self.assertEqual(handler.replies[0][0], 409)
                self.assertIn("reasoning_effort", handler.replies[0][1]["fields"])


class RevisionTests(unittest.TestCase):
    def test_each_distinct_version_is_recorded_once(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            seen, revisions = {}, []
            (out / "blog.md").write_text("draft")
            observe_revisions(out, seen, revisions, 1.0)
            observe_revisions(out, seen, revisions, 1.1)  # unchanged
            (out / "blog.md").write_text("draft, fixed")
            observe_revisions(out, seen, revisions, 2.0)
            self.assertEqual([r["bytes"] for r in revisions], [5, 12])


class SpecV2Tests(unittest.TestCase):
    def spec(self, **overrides):
        base = json.loads((ROOT / "experiment/sprint.sample.json").read_text())
        base.update(overrides)
        d = Path(tempfile.mkdtemp())
        sample = ROOT / "experiment"
        base["paths"] = {
            k: str((sample / v).resolve()) if v else None
            for k, v in base["paths"].items()
        }
        path = d / "spec.json"
        path.write_text(json.dumps(base))
        return path

    def fields(self, path):
        _, result, _, _ = inspect_spec(path)
        return {b["field"] for b in result["blockers"]}

    def test_v2_optional_fields_are_accepted(self):
        path = self.spec(
            temperature="omit",
            model_key_env="ANTHROPIC_API_KEY",
            price_per_mtok={"input": 4, "output": 20},
            phase_evidence="declared_or_observed",
        )
        blocked = self.fields(path)
        for field in (
            "temperature",
            "model_key_env",
            "price_per_mtok",
            "phase_evidence",
        ):
            self.assertNotIn(field, blocked)

    def test_replaced_checker_is_a_visible_warning(self):
        path = self.spec()
        _, result, _, _ = inspect_spec(path)
        self.assertEqual(result["warnings"], [])
        spec = json.loads(path.read_text())
        spec["paths"]["verifier"] = str(ROOT / "vendor/agent_blog_verifier/verify.py")
        path.write_text(json.dumps(spec))
        _, result, _, _ = inspect_spec(path)
        self.assertEqual([w["field"] for w in result["warnings"]], ["paths.verifier"])

    def test_bad_v2_fields_are_blockers(self):
        path = self.spec(
            model_key_env="lower-case",
            price_per_mtok={"input": -1},
            phase_evidence="whatever",
        )
        blocked = self.fields(path)
        self.assertTrue(
            {"model_key_env", "price_per_mtok", "phase_evidence"} <= blocked
        )


if __name__ == "__main__":
    unittest.main()
