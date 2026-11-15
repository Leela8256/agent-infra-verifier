# Agent infrastructure verifier

Leela's infrastructure component for the GTM framework experiment. It runs a trusted local agent command, records resources and execution events, and optionally grades its blog with Mithilesh's unchanged verifier.

**Implemented and tested:** local runner, watchdog budgets, telemetry receiver/SDK, quality integration, reproducibility records, and CSV/JSON summaries. CPU, elapsed time, and process memory come from real processes. The demonstration uses synthetic model/tool records and makes no paid API calls.

## Start here

Requires macOS or Linux, Python 3.11+, and psutil. From this repository:

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e . --no-deps
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/demo.py
~~~

The demo creates a new directory under runs/ with 13 scenarios, summary.csv, summary.json, and evidence for every attempt. Deliberate failures are expected. See [test results](docs/TEST_RESULTS.md).

Run one structural fixture:

~~~bash
.venv/bin/python -m infra_verifier run \
  --framework fixture --config configs/pilot.json \
  --experiment examples/experiment.json \
  --feed examples/feed.json --banned examples/banned_words.txt \
  --reference-time 2026-11-15T00:00:00Z \
  -- .venv/bin/python examples/fixture_agent.py success
~~~

## What it does

- Measures execution time, CPU-seconds, sampled process-tree RSS, and OS resource high-water statistics.
- Captures input/output tokens, model calls, tool calls, errors, retries, request durations, steps, and reported cost.
- Stops runs that exceed timeout, CPU, memory, token, call-count, step-count, or cost budgets.
- Detects remaining subprocesses, incomplete/malformed telemetry, grading errors, and mismatched model identity.
- Preserves the original quality verdict separately from infrastructure findings.

[METRICS.md](METRICS.md) defines each parameter. [INTEGRATION.md](docs/INTEGRATION.md) explains actual-agent instrumentation.

## Compatibility with Mithilesh

The vendored verify.py is byte-for-byte identical to [commit 6630669](https://github.com/mithileshgau/agent-blog-verifier/tree/6630669c083fcac811b04acdb6df644ff54a490c). Its MIT license and original documents are included. A test checks the original Git blob hash.

We call its run() function in a separate process with an explicit reference time. This freezes date-relative grading without modifying its code. Grading happens after resource measurement.

His metrics.md says infrastructure measurements do not rewrite quality verdicts. We preserve that separation. No LLM rubric judge has been implemented or run.

His current scope_respected check detects extra files inside output, not writes elsewhere. This runner also does not prove filesystem containment. Inherited date-boundary and URL-normalization behavior remains unchanged; settle it with Mithilesh before freezing the task.

## Actual agents

Replace the fixture command with a framework entrypoint. Python adapters can use Recorder; other languages can POST the same event schema.

OUTPUT_DIR identifies the final-artifact directory. INFRA_RUN_ID correlates events and external traces. Write only blog.md and meta.json to OUTPUT_DIR for the blog checker.

Supply the real frozen feed, word list, reference time, and experiment JSON. That file declares model, provider, temperature, max_tokens, and cache_policy. Reported model identity is checked against it. Other SDK settings are declarations: the runner does not configure or independently inspect the provider SDK.

Use --trace-url to retain an existing Langfuse trace URL. **Live Langfuse upload/export is not implemented.** Existing tracing can continue alongside this collector using INFRA_RUN_ID.

Do not put credentials in command arguments or experiment JSON: the manifest records them. Environment values are not copied into it. Agent stdout/stderr are captured as emitted.

## Evidence

~~~text
manifest.json       configuration, hashes, command, machine and model settings
report.json         metrics, quality, budgets, measurement validity
quality.json        original checker result, when enabled
events.jsonl        host-recorded adapter events
samples.jsonl       resource samples
stdout.log          agent stdout
stderr.log          agent stderr
inputs/             frozen grading inputs, checker and experiment config
output/             agent artifacts
~~~

Existing run directories are refused.

~~~bash
.venv/bin/python -m infra_verifier summarize runs/demo-*/*/report.json \
  --json comparison.json --csv comparison.csv
~~~

Different input/configuration/source/dependency/machine hashes stay in separate comparison groups. The demo deliberately uses different limits to test failures; it is not a framework leaderboard.

Cost per passing run includes failed-attempt costs. It stays null if any attempt has incomplete/unknown cost or no run passed. Three runs get median/range and sample token standard deviation, not a p95 estimate.

| CLI code | Meaning |
|---|---|
| 0 | Completed; measurements acceptable under config; quality passes or was not requested; no observed breach |
| 1 | Quality partial |
| 2 | Agent failure, budget breach, orphaned processes, or quality failure |
| 3 | Configuration/launch/collector error, required telemetry incomplete, or grading error |

Read the separate report fields; one exit code cannot capture every dimension.

## Limits of this version

- Local mode runs as your user. It is for trusted/cooperative commands and is **not a security sandbox**.
- Sampled tree RSS can double-count shared pages and miss peaks. wait4 high-water memory is separate and is not a simultaneous tree peak.
- CPU combines sampled process counters and wait4. Unobserved short-lived or detached children can be missed.
- Watchdog limits are reactive and can overshoot. Model/tool events arrive after an attempt; there are no pre-request cost reservations.
- Telemetry is adapter-reported. The agent receives the receiver token, so this is not tamper-resistant or provider-attested accounting.
- A finish event declares coverage; it cannot prove the absence of uninstrumented calls.
- GPU/VRAM, provider-side compute, network/disk totals, and filesystem-write auditing are not implemented.
- Docker's engine was unreachable. No container/cgroup backend was implemented or claimed as tested.
- Fixture prose and usage are synthetic. These tests demonstrate instrumentation and structural grading, not actual LLM performance.

## Before the scored sprint

1. Agree on real task inputs, exact rules, model settings, and budgets. Shipped limits are a pilot.
2. Instrument every SDK/tool attempt, including retries, and reconcile a trace with provider usage.
3. Preserve live Langfuse URLs.
4. Add and test a Linux/container backend when hard ceilings and write containment are required. Keep its measurements separate from local results.
5. Freeze the implementation and inputs; collect three runs per framework, preserving failures.

All six frameworks in Amitabh's latest list can invoke this runner. No framework-specific adapter or benchmark result is implied.
