"""Supervise one POSIX command, preserving resource and quality outcomes separately."""

import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import psutil

from . import __version__
from .findings import failure_tags
from .phases import PhaseEvidence
from .quality import DEFAULT_VERIFIER, evaluate
from .resources import ProcessTree, rusage_metrics
from .services import SharedServices, validate_upstream
from .telemetry import EventStore, Receiver
from .workspace import audit, snapshot


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def observe_revisions(output, seen, revisions, elapsed):
    """v2: record each new version of blog.md / meta.json the host can see."""
    for name in ("blog.md", "meta.json"):
        path = output / name
        try:
            if not path.is_file() or path.is_symlink():
                continue
            stat = path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
            if seen.get(name) == key:
                continue
            seen[name] = key
            digest = sha256(path)
        except OSError:
            continue
        if not revisions or not any(
            r["file"] == name and r["sha256"] == digest for r in revisions[-2:]
        ):
            revisions.append(
                {
                    "file": name,
                    "elapsed_s": round(elapsed, 3),
                    "sha256": digest,
                    "bytes": stat.st_size,
                }
            )


def run(
    command,
    run_dir,
    config,
    *,
    framework="unspecified",
    trial=1,
    cwd=None,
    feed=None,
    banned=None,
    reference_time=None,
    verifier=None,
    trace_url=None,
    experiment=None,
    telemetry_source="shared_services",
    model_upstream="https://openrouter.ai/api/v1",
    model_key_env="OPENROUTER_API_KEY",
    search_feed=None,
    task_prompt=None,
    filesystem_mode="audit",
    require_phases=False,
    langfuse="off",
    request_timeout_s=15,
    enforce_model_settings=False,
    sprint_id=None,
    phase_evidence="declared_or_observed",
):
    if phase_evidence not in {"declared", "declared_or_observed"}:
        raise ValueError("phase_evidence must be declared or declared_or_observed")
    if os.name != "posix":
        raise ValueError("local runner currently supports macOS and Linux")
    if not command:
        raise ValueError("provide an agent command after --")
    if filesystem_mode not in {"audit", "enforce"} or langfuse not in {
        "off",
        "required",
    }:
        raise ValueError("invalid filesystem or Langfuse mode")
    if (
        isinstance(request_timeout_s, bool)
        or not isinstance(request_timeout_s, (int, float))
        or not math.isfinite(request_timeout_s)
        or request_timeout_s <= 0
    ):
        raise ValueError("request timeout must be positive and finite")
    if enforce_model_settings and experiment is None:
        raise ValueError("model settings enforcement requires --experiment")
    if langfuse == "required":
        from .langfuse_export import settings

        settings()  # Fail before launching if credentials/project are absent.
    cwd = Path(cwd or Path.cwd()).resolve(strict=True)
    source_cwd = cwd
    if telemetry_source not in {"shared_services", "adapter_reported"}:
        raise ValueError("telemetry_source must be shared_services or adapter_reported")
    service_feed = None
    upstream_key = ""
    inputs = {}
    if task_prompt:
        path = Path(task_prompt).resolve(strict=True)
        inputs["prompt.txt"] = {"path": str(path), "sha256": sha256(path)}
    declared_experiment = None
    if experiment is not None:
        declared_experiment = json.loads(Path(experiment).read_text())
        if (
            not isinstance(declared_experiment, dict)
            or not {"model", "provider", "temperature", "max_tokens", "cache_policy"}
            <= declared_experiment.keys()
        ):
            raise ValueError(
                "experiment requires model, provider, temperature, max_tokens, and cache_policy"
            )
        for key in ("model", "provider", "cache_policy"):
            if (
                not isinstance(declared_experiment[key], str)
                or not declared_experiment[key].strip()
            ):
                raise ValueError(f"experiment {key} must be a nonempty string")
        from .telemetry import nonnegative

        # v2: temperature may be None ("omit"), for models that reject the parameter.
        if declared_experiment["temperature"] is not None:
            nonnegative(declared_experiment["temperature"], "temperature")
        effort = declared_experiment.get("reasoning_effort")
        if effort is not None and (not isinstance(effort, str) or not effort.strip()):
            raise ValueError("experiment reasoning_effort must be a nonempty string")
        prices = declared_experiment.get("price_per_mtok")
        if prices is not None:
            if not isinstance(prices, dict) or set(prices) != {"input", "output"}:
                raise ValueError("experiment price_per_mtok needs input and output")
            nonnegative(prices["input"], "price_per_mtok.input")
            nonnegative(prices["output"], "price_per_mtok.output")
        nonnegative(declared_experiment["max_tokens"], "max_tokens", integer=True)
        if declared_experiment["max_tokens"] == 0:
            raise ValueError("experiment max_tokens must be positive")
        path = Path(experiment).resolve(strict=True)
        inputs["experiment.json"] = {"path": str(path), "sha256": sha256(path)}
    if feed is not None:
        if banned is None or reference_time is None:
            raise ValueError(
                "quality integration requires --feed, --banned, and --reference-time"
            )
        date = datetime.fromisoformat(reference_time.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("reference-time requires a timezone")
        for name, path in {
            "feed.json": feed,
            "banned_words.txt": banned,
            "verify.py": verifier or DEFAULT_VERIFIER,
        }.items():
            resolved = Path(path).resolve(strict=True)
            inputs[name] = {"path": str(resolved), "sha256": sha256(resolved)}
        if not isinstance(json.loads(Path(feed).read_text()), list):
            raise ValueError("feed must be a JSON array")
    elif banned is not None or reference_time is not None or verifier is not None:
        raise ValueError("quality options require --feed")
    if telemetry_source == "shared_services":
        parsed = validate_upstream(model_upstream)
        path = (
            Path(search_feed or feed).resolve(strict=True)
            if search_feed or feed
            else None
        )
        if path is None:
            raise ValueError("shared services require --search-feed or --feed")
        service_feed = json.loads(path.read_text())
        if not isinstance(service_feed, list) or len(service_feed) < 3:
            raise ValueError("shared search requires at least three feed records")
        inputs["search_feed.json"] = {"path": str(path), "sha256": sha256(path)}
        upstream_key = os.environ.get(model_key_env, "")
        if not upstream_key and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError(f"set {model_key_env} in the supervisor environment")
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)  # Never overwrite previous evidence.
    cwd = run_dir / "workspace"
    cwd.mkdir()
    output = cwd / "output"
    output.mkdir()
    write_guard = {"backend": "none", "probe": "not_requested"}
    if filesystem_mode == "enforce":
        from .sandbox import probe

        write_guard = probe(output)
    (run_dir / "output").symlink_to("workspace/output", target_is_directory=True)
    before = snapshot(cwd)
    # Resolve executable and an explicit script path before entering the empty cwd.
    launch_command = list(command)
    if "/" in launch_command[0] and not Path(launch_command[0]).is_absolute():
        launch_command[0] = str(source_cwd / launch_command[0])
    if (
        len(launch_command) > 1
        and not launch_command[1].startswith("-")
        and (source_cwd / launch_command[1]).is_file()
    ):
        launch_command[1] = str(source_cwd / launch_command[1])
    frozen = run_dir / "inputs"
    frozen.mkdir()
    for name, source in inputs.items():
        shutil.copyfile(source["path"], frozen / name)
    run_id = str(uuid.uuid4())
    machine = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
    }
    source_hashes = {
        p.name: sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))
    }
    contract = {
        "config": asdict(config),
        "inputs": {k: v["sha256"] for k, v in inputs.items()},
        "reference_time": reference_time,
        "backend": "local-sampled",
        "machine": machine,
        "source_hashes": source_hashes,
        "psutil": psutil.__version__,
        "telemetry_source": telemetry_source,
        "model_upstream": model_upstream if service_feed is not None else None,
        "search_contract": "three-pages-first-valid-request-429-v1"
        if service_feed is not None
        else None,
        "workspace_audit": "fresh-cwd-before-after-v1",
        "filesystem_mode": filesystem_mode,
        "write_guard_backend": write_guard["backend"],
        "require_phases": require_phases,
        "langfuse": langfuse,
        "request_timeout_s": request_timeout_s,
        "enforce_model_settings": enforce_model_settings,
        "sprint_id": sprint_id,
        "phase_evidence": phase_evidence,
    }
    comparison_key = hashlib.sha256(
        json.dumps(contract, sort_keys=True).encode()
    ).hexdigest()
    manifest = {
        "schema_version": 1,
        "verifier_version": __version__,
        "run_id": run_id,
        "framework": framework,
        "trial": trial,
        "command": command,
        "cwd": str(cwd),
        "source_cwd": str(source_cwd),
        "launch_command": launch_command,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "config": asdict(config),
        "config_sha256": config.digest,
        "comparison_key": comparison_key,
        "inputs": inputs,
        "reference_time": reference_time,
        "machine": machine,
        "backend": "local-sampled",
        "trace_url": trace_url,
    }
    manifest.update(
        source_hashes=source_hashes,
        psutil_version=psutil.__version__,
        declared_experiment=declared_experiment,
        telemetry_source=telemetry_source,
        model_upstream=contract["model_upstream"],
        search_contract=contract["search_contract"],
        write_guard=write_guard,
        sprint_id=sprint_id,
    )
    write_json(run_dir / "manifest.json", manifest)
    store = EventStore(run_dir / "events.jsonl", run_id)
    phases = PhaseEvidence(store, output, run_dir / "phase-evidence")
    receiver = (
        SharedServices(
            store,
            service_feed,
            model_upstream,
            upstream_key,
            timeout=request_timeout_s,
            phases=phases,
            model_settings=declared_experiment if enforce_model_settings else None,
        )
        if service_feed is not None
        else Receiver(store)
    )
    receiver.start()
    child = tree = usage = None
    exit_code = None
    reason = "launch_error"
    cap_triggered = []
    measurement_errors = []
    samples = 0
    peak = sampled_cpu = 0
    started = time.monotonic()
    elapsed = 0
    orphan_count = 0
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("LANGFUSE_"):
            env.pop(key)
    if service_feed is not None:
        # The provider credential stays in the parent. No event receiver is exposed.
        for key in {
            model_key_env,
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "ANTHROPIC_API_KEY",
            "INFRA_EVENT_URL",
            "INFRA_EVENT_TOKEN",
        }:
            env.pop(key, None)
    env.update(receiver.environment)
    env.update(
        OUTPUT_DIR=str(output),
        INFRA_STARTED_MONOTONIC=str(started),
        PYTHONDONTWRITEBYTECODE="1",
    )
    store.started = started
    if task_prompt:
        env["INFRA_TASK_PROMPT"] = str(frozen / "prompt.txt")
    if declared_experiment is not None:
        # v2: owners no longer hard-code the frozen model id (a mismatch is a 409).
        env["INFRA_MODEL"] = declared_experiment["model"]
    launch_wall = datetime.now()
    revisions = []  # v2: host-observed versions of the output artifacts
    seen = {}
    if filesystem_mode == "enforce":
        from .sandbox import command as guarded_command

        launch_command = guarded_command(launch_command, output)
    try:
        with (
            (run_dir / "stdout.log").open("wb") as stdout,
            (run_dir / "stderr.log").open("wb") as stderr,
            (run_dir / "samples.jsonl").open("w") as sample_file,
        ):
            child = subprocess.Popen(
                launch_command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            tree = ProcessTree(child.pid)
            reason = "completed"
            while True:
                elapsed = time.monotonic() - started
                sample = tree.sample()
                sample_file.write(json.dumps({"elapsed_s": elapsed, **sample}) + "\n")
                sample_file.flush()
                samples += 1
                peak = tree.peak_rss / 2**20
                sampled_cpu = sample["cpu_time_s"]
                observe_revisions(output, seen, revisions, elapsed)
                pid, status, reaped = os.wait4(child.pid, os.WNOHANG)
                if pid:
                    usage = reaped
                    exit_code = os.waitstatus_to_exitcode(status)
                    child.returncode = exit_code
                    break
                hits = store.budget_hits(config)
                for key, value in [
                    ("wall_clock_s", elapsed),
                    ("cpu_time_s", sampled_cpu),
                    ("memory_mb", peak),
                ]:
                    limit = getattr(config, key)
                    if limit is not None and value > limit:
                        hits.append(key)
                if hits and config.enforce_budgets:
                    cap_triggered = sorted(set(hits))
                    reason = "budget_exceeded"
                    tree.kill()
                    _, status, usage = os.wait4(child.pid, 0)
                    exit_code = os.waitstatus_to_exitcode(status)
                    child.returncode = exit_code
                    elapsed = time.monotonic() - started
                    break
                time.sleep(config.sample_interval_s)
            if reason == "completed" and exit_code != 0:
                reason = "agent_error"
            orphan_count = len([p for p in tree.survivors() if p.pid != child.pid])
            if orphan_count:
                tree.kill()
                if reason == "completed":
                    reason = "orphaned_processes"
            measurement_errors.extend(sorted(set(tree.errors)))
    except OSError as error:
        measurement_errors.append(str(error))
        reason = "launch_error" if child is None else "supervisor_error"
    except KeyboardInterrupt:
        reason = "interrupted"
    finally:
        if child is not None and child.returncode is None:
            if tree is not None:
                tree.kill()
            else:
                child.kill()
            child.wait(timeout=5)
        receiver.close()
        store.close()
    observe_revisions(output, seen, revisions, time.monotonic() - started)
    write_json(
        run_dir / "artifact-revisions.json",
        {
            "method": "host sampling of output files every sample_interval_s; "
            "two writes inside one interval appear as one version",
            "revisions": revisions,
        },
    )
    denied = None
    if filesystem_mode == "enforce" and tree is not None:
        from .sandbox import denied_writes

        time.sleep(1)  # let the unified log flush the last denials
        raw_denied = denied_writes(
            {p for p, _ in tree.known}, launch_wall, datetime.now()
        )
        # Runtimes touch device files on start (Python writes /dev/dtracehelper, and the
        # log records it only sometimes). Those are not agent file writes: keep them in
        # the evidence file but do not count them.
        denied = (
            None
            if raw_denied is None
            else [d for d in raw_denied if not d["path"].startswith("/dev/")]
        )
        write_json(
            run_dir / "denied-writes.json",
            {
                "counted": denied,
                "raw": raw_denied,
                "rule": "count denied file writes by the agent's processes, excluding /dev/ device files",
            },
        )
    else:
        write_json(run_dir / "denied-writes.json", {"counted": None, "raw": None})
    after = snapshot(cwd)
    workspace_audit = audit(before, after)
    write_json(
        run_dir / "workspace-audit.json",
        {"before": before, "after": after, **workspace_audit},
    )
    measurement_errors.extend(workspace_audit["errors"])
    resource = (
        rusage_metrics(usage)
        if usage is not None
        else {"reaped_cpu_time_s": None, "reaped_peak_rss_mb": None}
    )
    cpu = (
        max(sampled_cpu, resource["reaped_cpu_time_s"] or 0)
        if usage is not None
        else None
    )
    metrics = {
        **store.metrics(),
        "wall_clock_s": elapsed,
        "cpu_time_s": cpu,
        "peak_rss_mb": peak if samples else None,
        **resource,
        "sample_count": samples,
        "hit_budget_cap": bool(cap_triggered),
        "orphaned_processes": orphan_count,
        "steps_source": "proxy_model_request_attempts"
        if service_feed is not None
        else "adapter_reported",
        "writes_outside_output": len(workspace_audit["changes_outside_output"]),
        # v2: blocked attempts were invisible before (the guard denies, nothing changes).
        "writes_blocked_by_guard": None if denied is None else len(denied),
        "observed_artifact_versions": {
            name: len({r["sha256"] for r in revisions if r["file"] == name})
            for name in ("blog.md", "meta.json")
        },
        **(
            receiver.search_metrics()
            if service_feed is not None
            else {
                "search_pages_fetched": None,
                "all_search_pages_fetched": None,
                "search_total_pages": None,
            }
        ),
    }
    # A short-lived leader's high-water RSS may exceed the polling samples.
    metrics["memory_observed_mb"] = (
        max(peak, resource["reaped_peak_rss_mb"] or 0) if samples or usage else None
    )
    if declared_experiment and any(
        model != declared_experiment["model"] for model in metrics["models_observed"]
    ):
        measurement_errors.append(
            "observed model differs from the declared experiment model"
        )
    violations = set(cap_triggered + store.budget_hits(config))
    for key, metric in [
        ("wall_clock_s", elapsed),
        ("cpu_time_s", cpu),
        ("memory_mb", metrics["memory_observed_mb"]),
    ]:
        if (
            getattr(config, key) is not None
            and metric is not None
            and metric > getattr(config, key)
        ):
            violations.add(key)
    missing = store.completeness()
    quality = {"verdict": "not_run", "checks": []}
    if feed is not None:
        quality = evaluate(
            frozen / "verify.py",
            output,
            frozen / "feed.json",
            frozen / "banned_words.txt",
            reference_time,
        )
        write_json(run_dir / "quality.json", quality)
    metrics["said_done_but_failed"] = exit_code == 0 and quality["verdict"] == "fail"
    workflow = phases.result(
        {
            name: sha256(output / name)
            for name in ("blog.md", "meta.json")
            if (output / name).is_file()
        }
    )
    blog_versions = metrics["observed_artifact_versions"]["blog.md"]
    if (
        phase_evidence == "declared_or_observed"
        and workflow["status"] == "missing_evidence"
    ):
        # v2: one-loop agents have no stage boundaries to declare. The host still sees
        # each saved version of blog.md; a later version is evidence of a revision,
        # not proof that a check happened or that it fixed anything.
        if blog_versions >= 2:
            workflow = {
                **workflow,
                "status": "observed_revisions",
                "provenance": "host-observed blog.md versions; no declared stage labels",
                "observed_blog_versions": blog_versions,
            }
        elif blog_versions == 1:
            workflow = {
                **workflow,
                "status": "single_version_observed",
                "provenance": "host saw one blog.md version: no visible draft->fix revision",
                "observed_blog_versions": 1,
            }
    metrics.update(
        workflow_phase_count=workflow["count"], workflow_status=workflow["status"]
    )
    infra_checks = [
        {
            "id": "working_directory_scope",
            "hard": True,
            "tag": "file_or_state_error",
            "result": "fail"
            if workspace_audit["changes_outside_output"]
            else "unscored"
            if workspace_audit["errors"]
            else "pass",
            "detail": workspace_audit["changes_outside_output"],
        },
        {
            "id": "said_done_but_failed",
            "hard": True,
            "tag": "said_done_but_failed",
            "result": "fail" if metrics["said_done_but_failed"] else "pass",
            "detail": "Proxy signal: exit code 0 combined with a failing quality verdict; not a claim about the agent's wording.",
        },
    ]
    if service_feed is not None:
        infra_checks.append(
            {
                "id": "all_search_pages_fetched",
                "hard": True,
                "tag": "looped_or_quit_early",
                "result": "pass" if metrics["all_search_pages_fetched"] else "fail",
                "detail": {
                    "fetched": metrics["search_pages_fetched"],
                    "required": [1, 2, 3],
                },
            }
        )
    if filesystem_mode == "enforce":
        infra_checks.append(
            {
                "id": "no_blocked_writes",
                "hard": True,
                "tag": "file_or_state_error",
                # A logged denial is certain. No logged denial is not proof of none: in
                # testing, the unified log missed a denial made by an agent process.
                "result": "unscored"
                if denied is None
                else "fail"
                if denied
                else "not_observed",
                "detail": denied
                if denied
                else "guard denials are not observable on this host"
                if denied is None
                else "no denial found in the macOS unified log (best effort: absence is not proof)",
            }
        )
    if require_phases:
        observed = workflow["status"] == "observed_revisions"
        single = workflow["status"] == "single_version_observed"
        infra_checks.append(
            {
                "id": "draft_check_fix_evidence",
                # v2: observed-only evidence never counts as a hard failure.
                "hard": not (observed or single),
                "tag": "looped_or_quit_early",
                "result": "pass"
                if workflow["status"] == "evidenced" or observed
                else "fail"
                if single
                else "unscored"
                if workflow["status"] == "missing_evidence"
                else "fail",
                "detail": workflow["issues"]
                if not (observed or single)
                else workflow["provenance"],
            }
        )
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "sprint_id": sprint_id,
        "framework": framework,
        "trial": trial,
        "comparison_key": comparison_key,
        "config_sha256": config.digest,
        "experiment_declared": declared_experiment is not None,
        "runtime_status": reason,
        "exit_code": exit_code,
        "measurement_status": "invalid"
        if measurement_errors or store.errors
        else ("incomplete" if missing and config.require_telemetry else "complete"),
        "measurement_errors": measurement_errors + store.errors,
        "telemetry_missing": missing,
        "telemetry_source": telemetry_source,
        "usage_source": "provider_usage_via_proxy"
        if service_feed is not None
        else "adapter_reported",
        "cost_source": (
            "provider_response_usage.cost, else frozen price table"
            if declared_experiment and declared_experiment.get("price_per_mtok")
            else "provider_response_usage.cost"
        )
        if service_feed is not None
        else "adapter_reported",
        "telemetry_scope": "Requests routed through this run's services only; direct provider/tool bypass is not network-blocked."
        if service_feed is not None
        else "cooperative adapter declarations",
        "resource_method": "local sampled process-tree RSS and CPU; wait4 CPU/high-water cross-check",
        "resource_limitations": [
            "RSS sum can double-count shared pages; peaks and short-lived children can be missed between samples.",
            "CPU is max(sampled per-process CPU sum, wait4 CPU of reaped leader and its waited descendants); detached unobserved processes can be missed.",
            "Limits are reactive watchdog stops, not kernel ceilings. Local mode is not a filesystem or network sandbox.",
        ],
        "budget_status": "exceeded"
        if violations
        else (
            "unknown"
            if measurement_errors or store.errors or missing
            else "within_observed_limits"
        ),
        "budget_violations": sorted(violations),
        "budget_stop_triggers": cap_triggered,
        "metrics": metrics,
        "quality": quality,
        # Which checker graded this run; is_upstream is False if paths.verifier replaced it.
        "quality_checker": {
            "sha256": inputs["verify.py"]["sha256"],
            "is_upstream": inputs["verify.py"]["sha256"] == sha256(DEFAULT_VERIFIER),
        }
        if "verify.py" in inputs
        else None,
        "infra_checks": infra_checks,
        "workspace_audit": workspace_audit,
        "write_guard": write_guard,
        "workflow": workflow,
        "requires_phase_evidence": require_phases,
        "requires_langfuse": langfuse == "required",
        "langfuse_export": {"status": "not_requested"},
        "model_rubric": {"status": "pending_external_review", "verdict": None},
        "trace_url": trace_url,
        "output_hashes": {
            name: sha256(output / name)
            for name in ("blog.md", "meta.json")
            if (output / name).is_file()
        },
    }
    report["failure_tags"], report["warning_tags"] = failure_tags(report)
    write_json(run_dir / "report.json", report)
    if langfuse == "required":
        from .langfuse_export import export

        report["langfuse_export"] = export(run_dir)
        report["trace_url"] = report["langfuse_export"].get("trace_url")
        write_json(run_dir / "report.json", report)
    return report
