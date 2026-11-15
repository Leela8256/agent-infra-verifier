"""Supervise one POSIX command, preserving resource and quality outcomes separately."""

import hashlib
import json
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
from .quality import DEFAULT_VERIFIER, evaluate
from .resources import ProcessTree, rusage_metrics
from .telemetry import EventStore, Receiver


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
):
    if os.name != "posix":
        raise ValueError("local runner currently supports macOS and Linux")
    if not command:
        raise ValueError("provide an agent command after --")
    cwd = Path(cwd or Path.cwd()).resolve(strict=True)
    inputs = {}
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

        nonnegative(declared_experiment["temperature"], "temperature")
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
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)  # Never overwrite previous evidence.
    output = run_dir / "output"
    output.mkdir()
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
    )
    write_json(run_dir / "manifest.json", manifest)
    store = EventStore(run_dir / "events.jsonl", run_id)
    receiver = Receiver(store)
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
    env.update(receiver.environment)
    env.update(
        OUTPUT_DIR=str(output),
        INFRA_STARTED_MONOTONIC=str(started),
        PYTHONDONTWRITEBYTECODE="1",
    )
    store.started = started
    try:
        with (
            (run_dir / "stdout.log").open("wb") as stdout,
            (run_dir / "stderr.log").open("wb") as stderr,
            (run_dir / "samples.jsonl").open("w") as sample_file,
        ):
            child = subprocess.Popen(
                command,
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
                if hits:
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
    report = {
        "schema_version": 1,
        "run_id": run_id,
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
        "telemetry_source": "adapter_reported",
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
        "trace_url": trace_url,
        "output_hashes": {
            name: sha256(output / name)
            for name in ("blog.md", "meta.json")
            if (output / name).is_file()
        },
    }
    write_json(run_dir / "report.json", report)
    return report
