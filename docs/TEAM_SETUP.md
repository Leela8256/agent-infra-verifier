# Teammate setup guide

Read [the rules](SPRINT_PROTOCOL.md) first. There are two distinct activities: an offline smoke test anyone can run now, and recorded trials after the team freezes the common settings. A passing smoke test is not a benchmark result.

## 1. Install and run the offline demonstration

```bash
git clone https://github.com/Leela8256/agent-infra-verifier.git
cd agent-infra-verifier
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e . --no-deps
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/demo_sprint.py
```

Python 3.11+ is required. Strict write guarding uses `sandbox-exec` on macOS or Landlock ABI >=3 on Linux x86_64/aarch64. No root/global security-setting changes are made. Unsupported hosts fail before launching a guarded agent. Windows owners need a supported common execution host.

The demo uses an offline model provider and local fake Langfuse endpoint. It leaves evidence under runs/sprint-demo-* and tests successful checkpoints, missing/wrong-order evidence, failed upload, and denied writes. No API key is needed.

## Try the populated sample, including three trials

[sprint.sample.json](../experiment/sprint.sample.json) contains concrete example model settings, budgets, shared inputs, an example owner, local Langfuse destination and grading procedure. [model-rubric.sample.json](../experiment/model-rubric.sample.json) provides an example post-run grading prompt and 0–2 grounding/factuality/readability criteria. Neither file represents a team decision. The sample uses synthetic output, a six-post feed and fake endpoints; it cannot produce a meaningful model grade.

```bash
.venv/bin/python tools/demo_frozen_sprint.py
```

This starts local fake services, resolves the sample paths, substitutes the actual host and local endpoint addresses, freezes an isolated bundle and records all three trials. It needs no external key. Evidence is under runs/frozen-demo-*. The checked-in sample remains draft; only the throwaway demo copy is marked ready. Fixed localhost ports in the sample are illustrative; running the sample directly does not start those servers.

## 2. Operator: fill in the shared decisions

One operator prepares the package for everyone:

```bash
cp experiment/sprint.template.json experiment/sprint.local.json
.venv/bin/python -m infra_verifier preflight --spec experiment/sprint.local.json
```

The initial nonzero exit is expected: it lists undecided fields. Fill them using [DECISIONS.md](DECISIONS.md). Input paths are relative to the settings file. You can also copy the filled sample as a starting point, but replace its fake model/provider/endpoints, owner, feed, reference time, host and provisional limits. Do not use synthetic-no-provider for a real agent. Supply the real shared feed and word list; examples/feed.json is a six-record test fixture, not the planned sprint feed.

Record the common host profile with:

```bash
.venv/bin/python -c 'import json,os,platform; print(json.dumps({"system":platform.system(),"machine":platform.machine(),"logical_cpus":os.cpu_count()}))'
```

Create the team's Langfuse project and obtain project keys through [Langfuse project settings](https://langfuse.com/docs/observability/get-started). Set these in the operator's environment using a secret manager or shell prompt, without committing values:

- OPENROUTER_API_KEY (the real upstream key)
- LANGFUSE_BASE_URL (the project's region/self-hosted base URL)
- LANGFUSE_PUBLIC_KEY
- LANGFUSE_SECRET_KEY
- LANGFUSE_PROJECT_ID (from the project URL)

The frozen JSON contains only the base URL and project ID. `sprint-run` rejects a different destination. The child does not receive Langfuse secrets or the real configured model key.

## 3. Each owner: connect the actual framework

Install the framework's dependencies before a run. The fresh working directory is not the repository; use absolute entrypoint/config paths. The framework may run from its own Python environment, Node, or another runtime. It does not need our Recorder SDK.

The supervisor injects:

| Environment variable | Owner must use it for |
|---|---|
| INFRA_TASK_PROMPT | Read the frozen prompt verbatim from this file |
| OUTPUT_DIR | Write blog.md and meta.json; this is the local mapping of the prompt's logical /output |
| INFRA_MODEL_BASE_URL | SDK base URL, including /v1 |
| INFRA_PROXY_KEY | Bearer key for model/search/checkpoint HTTP requests |
| INFRA_SEARCH_URL | Actual shared search tool endpoint |
| INFRA_PHASE_URL | Checkpoint transport for observing workflow phases |
| INFRA_RUN_ID | Trace correlation when adding framework-native spans |
| INFRA_MODEL | The frozen model id; pass it to the SDK instead of hard-coding one (a different id gets 409) |

OPENAI_BASE_URL/OPENROUTER_BASE_URL and API-key aliases are also injected, but SDKs may ignore them. Explicitly configure the provider adapter. Supported wire formats are Chat Completions and Responses, including SSE. Native Anthropic messages format is not implemented. An incompatible framework/provider configuration is a setup finding to document, not silently bypass via a direct provider key.

The proxy supplies frozen model/temperature/token settings when omitted and rejects explicit conflicts. If the spec freezes temperature as `"omit"` (Claude models), any temperature the agent sends, even 1, gets 409: turn off the framework's default temperature before the smoke test. `reasoning_effort` is rejected only when the spec freezes it to a different value. Configure caching, retries and other SDK settings exactly as agreed; those remain owner declarations. Search returns results/page/total_pages/next_page and intentionally returns 429 on the first valid request.

Keep the same frozen prompt. The common runtime mapping is: logical /output means OUTPUT_DIR, also ./output inside the fresh cwd. Explain that mapping in the framework's runtime/tool configuration, without changing the task content independently for each framework.

## 4. Observe draft, check and fix

Connect checkpoints to the framework's actual stage boundaries. Do not force stages to occur just to satisfy the checker or call the quality verifier inside the agent to repair it. Checkpoint transport is observability, not an additional task-solving tool.

At the end of draft, save the current blog.md to OUTPUT_DIR and POST `{"phase":"draft"}`. At the end of check, POST `{"phase":"check","findings":["actual issue"]}` or an empty findings array when no issues were found. At the end of fix, save the revised final files and POST `{"phase":"fix","resolution":"what changed, or why no change was necessary"}`.

All POSTs go to INFRA_PHASE_URL with the same proxy bearer key. The host copies artifacts, records hashes and evaluates the sequence only after exit. Additional internal check/fix cycles can be recorded. If a framework hides these boundaries, do not label three arbitrary model calls as the three stages.

**One-loop frameworks.** If you cannot declare stages, send no checkpoints. With the default `phase_evidence: "declared_or_observed"`, the host records every saved version of blog.md (`artifact-revisions.json`, sampled every 50 ms). Two or more distinct versions count as revision evidence. One version is a soft finding, `single_version_observed`. This shows the file changed, not that a check happened or fixed anything, so it never counts as a hard pass of the deck's draft/check/fix stressor. Saving blog.md in chunks also produces several versions. Declared checkpoints are stronger evidence where a framework has real stage boundaries.

**Writes outside /output.** With `--filesystem-mode enforce` on macOS, guard denials are read from the unified log after the run. A logged denial fails the hard check `no_blocked_writes`. Absence is reported as `not_observed`, never pass, because the log can miss denials. Runtime device files under `/dev/` are kept in `denied-writes.json` but not counted. On Linux (Landlock) there is no log, so the check is `unscored`.

For shell-capable wrappers, the installed CLI is a common transport:

```bash
# Run these INSIDE the supervised framework process, at the actual stage boundaries:
/absolute/path/to/verifier/.venv/bin/infra-verify phase draft
/absolute/path/to/verifier/.venv/bin/infra-verify phase check --findings-json '[]'
/absolute/path/to/verifier/.venv/bin/infra-verify phase fix --resolution 'No changes required after checking'
```

Language-neutral equivalent:

```bash
curl --fail-with-body "$INFRA_PHASE_URL" \
  -H "Authorization: Bearer $INFRA_PROXY_KEY" \
  -H 'Content-Type: application/json' -d '{"phase":"draft"}'
```

The labels are owner-declared. Snapshots establish ordering and artifact changes, not the agent's hidden reasoning or factual correctness. The external model rubric still applies.

## 5. Smoke test the real connection before freeze

Use the general `run` command in a separate smoke run directory with agreed candidate inputs. This is setup, not one of the scored trials:

```bash
.venv/bin/python -m infra_verifier run \
  --framework FRAMEWORK --run-dir runs/owner-smoke \
  --config configs/pilot.json --experiment /absolute/path/to/candidate-model-settings.json \
  --task-prompt /absolute/path/to/candidate-prompt.txt \
  --feed /absolute/path/to/candidate-feed.json --banned /absolute/path/to/banned_words.txt \
  --reference-time AGREED_ISO_TIMESTAMP \
  --filesystem-mode enforce --require-phases --langfuse required --enforce-model-settings \
  -- /absolute/path/to/framework-entrypoint
```

Candidate model JSON requires model/provider/temperature/max_tokens/cache_policy. Substitute actual values; placeholders are not executable. Pilot resource limits are provisional and must be replaced in the scored bundle.

Verify: one real provider call reports usage and cost; search 429/page coverage is visible; draft/check/fix evidence exists; the write guard permits output and denies outside writes; the Langfuse trace actually appears in the chosen project. An accepted upload alone is not a visibility check. Setup may require changes to adapters, but the presentation prohibits tuning the task prompt to pass the verifier.

## Freeze once

After decisions and setup are complete, set status to ready, then:

```bash
.venv/bin/python -m infra_verifier preflight --spec experiment/sprint.local.json
.venv/bin/python -m infra_verifier freeze --spec experiment/sprint.local.json --bundle experiment/frozen-v1
```

The operator shares the same bundle and Git commit with every owner. Freeze copies and hashes the prompt, resolved banned list, feed, checker and configs. If paths.rubric is supplied, it also copies and hashes that file as model-rubric.json. Do not edit a frozen bundle; changes require a new experiment. Runtime source hashes must match the freeze. Keep secrets out of the shared package.

## 6. Run exactly three recorded attempts

On the agreed execution host, with the operator's credentials set:

```bash
.venv/bin/python -m infra_verifier sprint-run --bundle experiment/frozen-v1 --framework FRAMEWORK --trial 1 -- /absolute/path/to/framework-entrypoint
.venv/bin/python -m infra_verifier sprint-run --bundle experiment/frozen-v1 --framework FRAMEWORK --trial 2 -- /absolute/path/to/framework-entrypoint
.venv/bin/python -m infra_verifier sprint-run --bundle experiment/frozen-v1 --framework FRAMEWORK --trial 3 -- /absolute/path/to/framework-entrypoint
```

Use a roster identifier from framework_owners. Do not connect the three commands with `&&`: a failed first attempt must not silently prevent recording the remaining attempts. No human help during a trial. Each trial starts fresh and each numbered directory is non-overwritable. Keep failed attempts and their logs.

Native write guarding can reveal framework assumptions about writable caches, home directories or temp files. Do not weaken it only for one owner. Record the incompatibility and agree any common protocol change before new scored runs.

## 7. Trace replay, summaries and handoff

If only trace upload failed, replay the saved evidence; do not rerun the agent:

```bash
.venv/bin/python -m infra_verifier export-langfuse runs/SPRINT/FRAMEWORK/trial-1
.venv/bin/python -m infra_verifier summarize runs/SPRINT/*/trial-*/report.json --json runs/comparison.json --csv runs/comparison.csv
```

Langfuse receives run/model/search/phase spans with provider usage and correlated run ID. Scored mode also captures bounded model input/output and phase snapshots for trace review. The payload is saved locally before upload. Export retry uses stable trace/span IDs. Proxy totals remain canonical; do not add Langfuse's inferred usage/cost on top.

Each owner brings the three deterministic results, model-rubric results, Langfuse links, failure diagnoses, setup minutes/dependencies/agent code size/tracing effort, and one lesson. Fill [owner-handoff.template.json](../experiment/owner-handoff.template.json). The model rubric is performed after the run under the frozen quality-review procedure. This harness explicitly leaves it pending; do not call a script pass the complete experiment score.

## Exit codes

0: infrastructure collection/checks acceptable and deterministic quality passes (or not requested in smoke mode). 1: deterministic quality partial. 2: agent/budget/hard finding. 3: setup/measurement/grading error, missing required workflow evidence, or failed required trace export. Read the report fields; model-rubric review and live trace visibility still require the handoff checklist.
