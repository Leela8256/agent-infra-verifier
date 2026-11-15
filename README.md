# Agent infrastructure verifier

Leela's infrastructure component for the GTM framework experiment. It runs a trusted local agent command, measures resources, and grades its blog with Mithilesh's unchanged verifier.

**Sprint default: shared HTTP services.** Model usage comes from the supervisor's proxy; the frozen search service records its own attempts, planted 429, retries, and three-page coverage. Owners configure endpoints and credentials, without adding Recorder calls.

## Start here

Requires macOS or Linux, Python 3.11+, and psutil. From this checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e . --no-deps
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/demo_shared.py
```

The shared demo runs eight scenarios over real HTTP against a local fake provider. Token/cost values and blog prose are synthetic; it makes no paid API calls. The older adapter demonstration remains available through `tools/demo.py`. [Test evidence](docs/TEST_RESULTS.md).

## Run a sprint agent

Set the real `OPENROUTER_API_KEY` in the supervisor's environment using your usual secret handling. Then:

```bash
.venv/bin/python -m infra_verifier run \
  --framework your-framework --config configs/pilot.json \
  --experiment /absolute/path/to/frozen-experiment.json \
  --feed /absolute/path/to/frozen-feed.json \
  --banned /absolute/path/to/banned_words.txt \
  --reference-time 2026-11-15T00:00:00Z \
  -- /absolute/path/to/agent-entrypoint
```

The child receives these per-run settings:

| Setting | Use |
|---|---|
| `INFRA_MODEL_BASE_URL` | Model SDK base URL; proxy supports `/chat/completions` and `/responses`, including SSE streams |
| `INFRA_PROXY_KEY` | Local bearer credential for model and search endpoints |
| `INFRA_SEARCH_URL` | Shared frozen search endpoint; GET `?page=1` or POST `{"page":1}` |
| `INFRA_RUN_ID` | Run identity for trace correlation; the proxy binds it server-side |
| `OUTPUT_DIR` | Absolute artifact directory; also available as `./output` in the fresh cwd |

`OPENAI_BASE_URL`, `OPENROUTER_BASE_URL`, and their API-key variables are also set to the local proxy settings. Frameworks that ignore these variables must explicitly configure their SDK's base URL/key. Native Anthropic and other API formats are not supported by this proxy. Python, JavaScript, CLI, or other HTTP clients can use it. No framework-specific adapter is claimed as tested.

The real upstream key stays in the parent; the child receives a run-scoped proxy key. Other directly inherited provider credentials with common names are removed. This is a local trusted-process setup, not a security boundary against a malicious process running as your user.

By default the proxy forwards to OpenRouter. Set `--model-upstream` and `--model-key-env` for another compatible backend. A separate `--search-feed` is available when quality grading is not requested.

[Integration contract and curl example](docs/INTEGRATION.md) · [Parameter definitions](METRICS.md)

## Accounting and checks

- **Canonical tokens/cost:** provider response usage captured by the proxy. No client-supplied usage and no Langfuse totals are merged into these fields. Missing values remain null.
- **Search:** the first valid request gets HTTP 429. The same run's successful page responses are tracked as a set; repeating one page cannot satisfy all three pages.
- **Resources:** elapsed time, CPU-seconds, sampled process-tree RSS, and OS high-water statistics.
- **Budgets:** reactive watchdog stops for time, CPU, memory, tokens, calls, steps, and reported cost.
- **Working directory:** every run starts in a fresh retained directory. A before/after audit flags persistent changes outside its `output` subtree, including symlink changes. It does not detect writes elsewhere on the machine or temporary writes deleted before exit.
- **False completion:** `said_done_but_failed` means exit code 0 plus quality verdict fail. This is an exit-status proxy, not a language analysis of the agent's final message.
- **Reports:** upstream quality verdict, infrastructure checks, measurement validity, and budgets stay separate. CSV `failure_tags` contains hard failures; `warning_tags` contains soft-only failures.

`steps` in shared mode means model request attempts, including retries. It does not expose framework-internal reasoning steps. `steps_source` records this definition; legacy adapter runs stay in different comparison groups.

Langfuse remains a trace viewer/correlation layer. Use `INFRA_RUN_ID` in traces and `--trace-url` to retain a known link. Live Langfuse export/import is not implemented. **The proxy is the token and cost source of truth for comparisons.**

## Evidence and summaries

Each fresh run directory includes `manifest.json`, `report.json`, `quality.json`, host-written `events.jsonl`, `samples.jsonl`, agent logs, frozen `inputs/`, `workspace-audit.json`, and `workspace/output/`. `output` at the run root is a convenience symlink. Existing run directories are refused.

```bash
.venv/bin/python -m infra_verifier summarize runs/shared-*/*/report.json \
  --json comparison.json --csv comparison.csv
```

CSV includes steps, errors, retry recovery, budget-stop status, time to first tool call, orphaned processes, page coverage, scope changes, false completion, and separate hard/soft tags. Input/configuration/source/dependency/machine/telemetry-source hashes keep incompatible runs in separate groups. The demos deliberately vary budgets; they are not a framework leaderboard.

Cost per quality-passing completed run includes failed-attempt costs. It is null if any attempt has incomplete cost or no run qualifies. Infrastructure checks are separately visible, even for quality-passing runs.

| CLI code | Meaning |
|---|---|
| 0 | Completed, measurements acceptable, quality passes or not requested, no hard infrastructure finding/budget breach |
| 1 | Quality partial (soft failures) |
| 2 | Agent failure, budget breach, hard infrastructure finding, or quality failure |
| 3 | Configuration/launch/collector error, required telemetry incomplete, or grading error |

## Mithilesh's contribution and legacy mode

The vendored checker is byte-for-byte identical to [commit 6630669](https://github.com/mithileshgau/agent-blog-verifier/tree/6630669c083fcac811b04acdb6df644ff54a490c). Its MIT license and original documents are included. A test verifies the Git blob hash. It runs after resource collection with an explicit grading date. No LLM rubric judge is implemented.

The earlier cooperative Recorder interface is retained only via `--telemetry-source adapter_reported`. It is not the sprint default. It produces `adapter_reported` provenance and separate comparison groups. To run the original fixture:

```bash
.venv/bin/python -m infra_verifier run --framework fixture \
  --telemetry-source adapter_reported -- .venv/bin/python examples/fixture_agent.py success
```

## Remaining limits

Local mode is not a filesystem or network sandbox. The workspace audit is partial; direct provider/tool bypass is not blocked. Proxy coverage means requests routed through it, not proof of all activity. Host-recorded HTTP evidence removes self-reported usage from the sprint path, but same-user processes could still tamper with local files.

RSS can double-count shared pages and miss peaks; detached or short-lived children can be missed. Watchdog stops can overshoot. The proxy counts completed attempts, and token/cost limits cannot reserve future spending. In-flight requests are drained for accounting after agent exit, with bounded wait; this drain is excluded from agent wall time.

No container/cgroup backend, GPU/VRAM, disk/network totals, live Langfuse sync, or real six-framework benchmark is claimed. The real frozen feed, SDK endpoint configuration, and provider usage must be smoke-tested by each owner before scored runs. Keep the same model/settings, search feed, limits, and verifier version across all three trials per framework.
