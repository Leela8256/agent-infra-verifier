"""Validate team decisions, freeze one bundle, and launch recorded sprint trials."""

import hashlib
import json
import math
import os
import platform
import re
import shutil
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .quality import DEFAULT_VERIFIER
from .runner import run, sha256, write_json

LIMITS = (
    "wall_clock_s",
    "cpu_time_s",
    "memory_mb",
    "model_calls",
    "tool_calls",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "steps",
)
REQUIRED = (
    "sprint_id",
    "framework_owners",
    "model",
    "provider",
    "temperature",
    "max_tokens",
    "cache_policy",
    "request_timeout_s",
    "sdk_retry_policy",
    "reference_time",
    "budget_policy",
    "execution_host",
    "langfuse_base_url",
    "langfuse_project_id",
    "quality_review_protocol",
    "paths",
    "budgets",
)


def code_hashes():
    return {p.name: sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))}


def safe_name(value):
    return isinstance(value, str) and bool(
        re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", value)
    )


def inspect_spec(path):
    path = Path(path).resolve()
    spec = json.loads(path.read_text())
    if not isinstance(spec, dict):
        raise ValueError("sprint spec must be an object")
    blockers = []

    def issue(field, reason):
        blockers.append({"field": field, "reason": reason})

    for field in REQUIRED:
        if spec.get(field) is None or spec.get(field) == "":
            issue(field, "team decision required")
    for field in (
        "model",
        "provider",
        "cache_policy",
        "sdk_retry_policy",
        "quality_review_protocol",
        "langfuse_project_id",
    ):
        if not isinstance(spec.get(field), str) or spec[field].strip().lower() in {
            "",
            "tbd",
            "todo",
            "undecided",
        }:
            issue(field, "replace the placeholder with the agreed setting")
    from .services import validate_upstream

    for field in ("langfuse_base_url", "model_upstream"):
        try:
            validate_upstream(spec.get(field) or "")
        except ValueError as error:
            issue(field, str(error))
    if spec.get("status") != "ready":
        issue(
            "status",
            "draft: finish the decisions and set status to ready before freezing",
        )
    if spec.get("schema_version") != 1:
        issue("schema_version", "must be 1")
    if not safe_name(spec.get("sprint_id")):
        issue("sprint_id", "use a short letters/digits/dash/underscore identifier")
    owners = spec.get("framework_owners")
    if (
        not isinstance(owners, dict)
        or not owners
        or any(
            not safe_name(k) or not isinstance(v, str) or not v.strip()
            for k, v in owners.items()
        )
    ):
        issue(
            "framework_owners",
            "supply the final framework roster and an owner for each",
        )
    if spec.get("budget_policy") not in {"record_and_stop", "record_only"}:
        issue(
            "budget_policy",
            "choose record_and_stop or record_only; this is separate from quality scoring",
        )
    for key in ("temperature", "max_tokens", "request_timeout_s"):
        value = spec.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or (key != "temperature" and value == 0)
            or (key == "max_tokens" and type(value) is not int)
        ):
            issue(key, "requires a finite valid model setting")
    budgets = spec.get("budgets") if isinstance(spec.get("budgets"), dict) else {}
    for key in LIMITS:
        value = budgets.get(key)
        if value is None:
            issue(
                "budgets." + key,
                "choose a positive ceiling or explicitly write disabled",
            )
        elif value == "disabled" and key in {"wall_clock_s", "steps", "cost_usd"}:
            issue(
                "budgets." + key,
                "a concrete wall-time, step and cost budget is required by this sprint protocol",
            )
    try:
        config = Config(
            profile=str(spec.get("sprint_id") or "draft"),
            **{
                k: None if budgets.get(k) == "disabled" else budgets.get(k)
                for k in LIMITS
            },
            enforce_budgets=spec.get("budget_policy") == "record_and_stop",
        )
    except (TypeError, ValueError) as error:
        issue("budgets", str(error))
        config = None
    date = None
    try:
        date = datetime.fromisoformat(
            spec.get("reference_time", "").replace("Z", "+00:00")
        )
        if date.tzinfo is None:
            raise ValueError()
    except (ValueError, AttributeError):
        issue("reference_time", "use an explicit ISO timestamp with timezone")
    files = {}
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    for name in ("prompt", "feed", "banned", "rubric"):
        value = paths.get(name)
        if name == "rubric" and value is None:
            continue
        if not isinstance(value, str) or not value:
            issue("paths." + name, "choose the shared input file")
            continue
        source = (path.parent / value).resolve()
        if not source.is_file():
            issue("paths." + name, "input file does not exist")
            continue
        files[name] = source
    banned_words = []
    if "banned" in files:
        banned_words = [
            x.strip() for x in files["banned"].read_text().splitlines() if x.strip()
        ]
        if not banned_words:
            issue("paths.banned", "the banned-word list must not be empty")
    if "feed" in files:
        try:
            feed = json.loads(files["feed"].read_text())
            if (
                not isinstance(feed, list)
                or len(feed) < 3
                or any(not isinstance(p, dict) for p in feed)
            ):
                raise ValueError("feed must contain at least three post objects")
            # The deck specifies about 30 posts, so the exact count is explicitly frozen.
            if spec.get("feed_record_count") != len(feed):
                issue(
                    "feed_record_count",
                    "declare the exact count of the final feed (deck: about 30)",
                )
            for post in feed:
                if not isinstance(post.get("topic"), str) or not post["topic"].strip():
                    raise ValueError("each feed post needs a topic string")
                score = post.get("score")
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(score)
                ):
                    raise ValueError("each feed post needs a finite numeric score")
                timestamp = datetime.fromisoformat(
                    post.get("published_at", "").replace("Z", "+00:00")
                )
                if timestamp.tzinfo is None:
                    raise ValueError("feed timestamps require a timezone")
            topics = [p.get("topic").strip().lower() for p in feed]
            if len(set(topics)) == len(topics):
                issue("paths.feed", "the duplicate-topic trap is missing")
            if all(p.get("url") for p in feed):
                issue("paths.feed", "the missing-URL trap is missing")
            if date is not None and date.tzinfo is not None:
                recent, old = [], []
                for post in feed:
                    timestamp = datetime.fromisoformat(
                        post["published_at"].replace("Z", "+00:00")
                    )
                    if date - timedelta(days=7) <= timestamp <= date and post.get(
                        "url"
                    ):
                        recent.append(post)
                    elif timestamp < date - timedelta(days=7):
                        old.append(post)
                if len({p["topic"].strip().lower() for p in recent}) < 3:
                    issue("paths.feed", "need at least three eligible distinct topics")
                if (
                    not old
                    or not recent
                    or max(p["score"] for p in old) < max(p["score"] for p in recent)
                ):
                    issue("paths.feed", "the old high-score trap is missing")
            feed_text = json.dumps(feed).lower()
            if banned_words and not any(
                re.search(r"\b" + re.escape(w.lower()) + r"\b", feed_text)
                for w in banned_words
            ):
                issue("paths.feed", "the banned-word trap is missing")
        except (ValueError, TypeError, AttributeError) as error:
            issue("paths.feed", str(error))
    host = spec.get("execution_host")
    if (
        not isinstance(host, dict)
        or not {"system", "machine", "logical_cpus"} <= host.keys()
    ):
        issue(
            "execution_host",
            "record system, machine and logical_cpus for the shared execution host",
        )
    return spec, {"ready": not blockers, "blockers": blockers}, files, config


def freeze(spec_path, bundle):
    spec, result, files, config = inspect_spec(spec_path)
    if not result["ready"]:
        raise ValueError(
            "sprint is not ready: "
            + "; ".join(f"{x['field']}: {x['reason']}" for x in result["blockers"])
        )
    bundle = Path(bundle).resolve()
    bundle.mkdir(parents=True, exist_ok=False)
    for name, source in files.items():
        shutil.copyfile(
            source,
            bundle
            / {
                "prompt": "prompt.txt",
                "feed": "feed.json",
                "banned": "banned_words.txt",
                "rubric": "model-rubric.json",
            }[name],
        )
    prompt = (bundle / "prompt.txt").read_text()
    banned = [
        line.strip()
        for line in (bundle / "banned_words.txt").read_text().splitlines()
        if line.strip()
    ]
    (bundle / "prompt.txt").write_text(
        prompt.replace("{{BANNED_WORDS}}", ", ".join(banned))
    )
    shutil.copyfile(DEFAULT_VERIFIER, bundle / "verify.py")
    write_json(bundle / "protocol.json", spec)
    write_json(bundle / "config.json", asdict(config))
    write_json(
        bundle / "experiment.json",
        {
            k: spec[k]
            for k in ("model", "provider", "temperature", "max_tokens", "cache_policy")
        },
    )
    hashes = {p.name: sha256(p) for p in bundle.iterdir() if p.is_file()}
    write_json(
        bundle / "lock.json",
        {
            "files": hashes,
            "verifier_sources": code_hashes(),
            "bundle_id": hashlib.sha256(
                json.dumps(hashes, sort_keys=True).encode()
            ).hexdigest(),
        },
    )
    return {"bundle": str(bundle), "sprint_id": spec["sprint_id"], "status": "frozen"}


def run_trial(bundle, framework, trial, run_root, command):
    from .langfuse_export import settings

    bundle = Path(bundle).resolve(strict=True)
    lock = json.loads((bundle / "lock.json").read_text())
    if lock["verifier_sources"] != code_hashes():
        raise ValueError(
            "verifier changed since freeze; use the frozen commit or start a new experiment"
        )
    for name, expected in lock["files"].items():
        if Path(name).name != name or sha256(bundle / name) != expected:
            raise ValueError("frozen bundle was changed: " + name)
    spec = json.loads((bundle / "protocol.json").read_text())
    if framework not in spec["framework_owners"] or trial not in {1, 2, 3}:
        raise ValueError("choose a frozen framework and trial 1, 2, or 3")
    actual_host = {
        "system": platform.system(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
    }
    if spec["execution_host"] != actual_host:
        raise ValueError("execution host differs from the frozen profile")
    base, _, _, project = settings()
    if (
        base != spec["langfuse_base_url"].rstrip("/")
        or project != spec["langfuse_project_id"]
    ):
        raise ValueError("Langfuse destination differs from the frozen project")
    directory = (
        Path(run_root).resolve() / spec["sprint_id"] / framework / f"trial-{trial}"
    )
    report = run(
        command,
        directory,
        Config.load(bundle / "config.json"),
        framework=framework,
        trial=trial,
        feed=bundle / "feed.json",
        banned=bundle / "banned_words.txt",
        task_prompt=bundle / "prompt.txt",
        experiment=bundle / "experiment.json",
        reference_time=spec["reference_time"],
        verifier=bundle / "verify.py",
        filesystem_mode="enforce",
        require_phases=True,
        langfuse="required",
        request_timeout_s=spec["request_timeout_s"],
        enforce_model_settings=True,
        sprint_id=spec["sprint_id"],
        model_upstream=spec.get("model_upstream", "https://openrouter.ai/api/v1"),
    )
    report["protocol_bundle_id"] = lock["bundle_id"]
    report["owner"] = spec["framework_owners"][framework]
    from .cli import result_exit_code

    report["trial_status"] = (
        "recorded_with_findings"
        if result_exit_code(report)
        else "awaiting_model_rubric_review"
    )
    write_json(directory / "report.json", report)
    return report
