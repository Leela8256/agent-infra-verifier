# Agent infrastructure verifier

**The included sample is a synthetic demonstration, not a real agent or a frozen team experiment.** Its model, owner, limits, six-post feed, Langfuse destination and grading rubric are examples. No real model API is called by the sample; no complete benchmark score is claimed.

Start with [the filled settings sample](experiment/sprint.sample.json), [the sample model-grading rubric](experiment/model-rubric.sample.json), and [the teammate setup guide](docs/TEAM_SETUP.md).

To get started with a **real agent**:

1. Clone/install using the commands below; run the offline demo to check the harness.
2. Copy experiment/sprint.template.json or sprint.sample.json to sprint.local.json. Agree the real model/settings, budgets, shared feed/banned words, owners, execution host, Langfuse project and grading procedure. Replace every sample-specific value; [DECISIONS.md](docs/DECISIONS.md) explains each field.
3. Install your framework and use an absolute entrypoint. Configure its model SDK, search tool, output directory and phase checkpoints using the injected environment variables in [the owner integration steps](docs/TEAM_SETUP.md#3-each-owner-connect-the-actual-framework). The harness does not implement your framework agent.
4. Set the real model and Langfuse keys in the supervisor environment. Smoke-test the actual agent and confirm the trace appears in the real Langfuse project.
5. Run preflight, freeze the common bundle, then run sprint-run for trials 1, 2 and 3. Preserve failures and complete the external model-rubric review. [Exact commands](docs/TEAM_SETUP.md#freeze-once) are in the guide.

The [sprint protocol](docs/SPRINT_PROTOCOL.md) maps the presentation's rules to the implementation. [Undecided settings](docs/DECISIONS.md) are explicit; the repo is not yet a frozen scored experiment.

This is Leela's infrastructure harness around Mithilesh's unchanged deterministic blog checker. It measures agent execution and records failures. It does not repair the agent, change its prompt, or rerun failed trials.

## Run the offline smoke test

Requires Python 3.11+ on macOS or Linux. From a fresh checkout:

```bash
git clone https://github.com/Leela8256/agent-infra-verifier.git
cd agent-infra-verifier
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e . --no-deps
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/demo_sprint.py
# Also exercise the populated sample: freeze + three trials.
.venv/bin/python tools/demo_frozen_sprint.py
```

The demo uses fake model and Langfuse endpoints. It makes real HTTP requests, captures phase artifacts, and exercises the native write guard. Its prose, tokens and costs are synthetic. It does not establish real-provider or framework compatibility.

## What is implemented

- Shared model proxy: canonical provider-reported tokens/cost, model/tool attempt logs, retry/error accounting, streaming support, and frozen model/temperature/token settings on scored runs.
- Shared frozen search: three pages and a planted first-call 429. Successful page coverage is measured server-side.
- Real process CPU, memory and time, with explicitly selected record-only or stop-on-budget policy.
- Native per-process write guard: macOS Seatbelt or Linux Landlock ABI >=3, checked before launch. The before/after working-directory audit remains supplementary.
- Draft/check/fix checkpoints with host-captured artifact snapshots, ordering/change checks, and explicit missing-evidence status. Model-request count is separate from workflow phases.
- Langfuse OTLP export for run, model, search and phase observations. Failed/partial exports remain visible and can be replayed without rerunning the agent.
- Preflight, immutable input bundle, code/input hash checks, and three numbered trial slots per framework. Existing trial directories cannot be overwritten.
- CSV/JSON summaries with separate quality, runtime, measurement, budgets, workflow evidence, hard failures and soft warnings.

## Prepare a real sprint

```bash
cp experiment/sprint.template.json experiment/sprint.local.json
.venv/bin/python -m infra_verifier preflight --spec experiment/sprint.local.json
```

The template intentionally fails preflight until the team supplies the model/settings, real shared inputs, budgets, execution-host profile, Langfuse destination, owners and quality-review procedure. Secrets belong in the parent environment, never in JSON or Git.

When all decisions are complete, follow [freeze and run](docs/TEAM_SETUP.md#freeze-once) in the setup guide. Do not use `configs/pilot.json` or the six-record example feed for scored results: those are development fixtures. The final feed is about 30 posts, as specified by the presentation.

## Important boundaries

The presentation's semantic workflow is draft/check/fix. Checkpoint labels are declared by the owner and backed by snapshots; they do not prove hidden reasoning or the correctness of self-review. Missing phase evidence cannot produce an infrastructure success exit code.

The native guard blocks common file creation, content writes, truncation, removal and symlink escapes outside output, with inherited stdout/stderr and `/dev/null` permitted for runtime I/O. Linux Landlock does not restrict chmod/chown/timestamps/xattrs; this is not a claim of full metadata containment or an adversarial sandbox. Reads and network access remain unrestricted. These limitations and tool-allowlist verification must be included in the team review.

Langfuse export is implemented against its documented OTLP/HTTP JSON API. The team must verify one live trace appears in its project; HTTP acceptance is not proof of UI visibility. Proxy usage remains canonical even if Langfuse estimates a different cost. Project keys are not available in this development environment, so retained integration evidence uses a local test endpoint.

The model-graded grounding/factuality/readability rubric belongs to the quality layer and remains an external post-run review. `quality.verdict` is the deterministic checker result, not the complete slide-8 score. Every report explicitly marks the model rubric pending; a CLI exit 0 does not certify the missing rubric.

## Other documentation

- [Presentation rules and implementation map](docs/SPRINT_PROTOCOL.md)
- [Team setup and exact commands](docs/TEAM_SETUP.md)
- [Settings still requiring a team decision](docs/DECISIONS.md)
- [HTTP and phase integration contract](docs/INTEGRATION.md)
- [Metric definitions](METRICS.md)
- [Validation and evidence](docs/TEST_RESULTS.md)

Mithilesh's checker and MIT license are preserved in `vendor/agent_blog_verifier/`. Its original Git blob hash is tested. Legacy Recorder mode remains available with explicit `--telemetry-source adapter_reported`; it is not allowed by the scored-trial command and is never pooled with shared-service runs.
