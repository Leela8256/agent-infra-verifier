# Sprint protocol derived from the presentation

Source: **Building an agent-native team at RocketRide**, 10-page presentation supplied by the user. Its SHA-256 and page references are recorded in [source.json](../experiment/source.json). The PDF itself is not redistributed. This document distinguishes source rules, implementation choices, and still-unresolved decisions.

## Rules already specified by the presentation

| ID | PDF page | Source rule | Implementation / evidence |
|---|---:|---|---|
| R01 | 3 | One job, explicit tools, scoped filesystem, least-privilege credentials | Owners configure only the agreed tools. Per-run proxy credentials replace provider keys in the child. Native write guard and audit; network/read isolation and complete allowlist verification remain out of scope. |
| R02 | 3 | Step and cost guardrails | Team chooses common limits and record/stop interpretation before freeze; proxy request attempts and semantic workflow phases are explicitly separate. |
| R03 | 4 | Write verifier first, then freeze; agent runs without help | Code and input hashes in frozen bundle; no agent repair, prompt optimization or rerun loop in the verifier. |
| R04 | 5–6 | Same prompt, model, tools and budget; one owner per framework | One bundle/config/owner roster; requested model, temperature and output-token limit enforced at the shared proxy. Other SDK settings are declared and checked at owner smoke test. |
| R05 | 6 | Three runs per framework, no human help during a run | `sprint-run --trial 1/2/3`; each slot refuses overwrites. Operator records all attempts, including failures. No-human-help is an operational rule, not electronically provable. |
| R06 | 6 | Top three AI-agent topics from last seven days by score; use all results | Frozen prompt, feed and reference time; original quality checker plus server-side page coverage. |
| R07 | 6 | 600–800 words, title/intro/topic sections/sources; blog.md and meta.json | Mithilesh's deterministic checker, unchanged. Exact checker behavior takes precedence over assumptions; freeze discrepancies with quality owner before Day 1. |
| R08 | 6–7 | Frozen feed of about 30 posts; old/duplicate/missing-URL/banned-word traps | Team supplies the actual feed and word list. Example feed is only a six-record unit fixture. Preflight checks schema/count, three eligible topics, and old/high-score, duplicate, missing-URL and banned-word traps; owner reviews the final task content. |
| R09 | 7 | First search call returns 429; three pages | Shared tool, reset for each run; successful-page set and recorded error/retry. Invalid page requests get 400 before the planted first valid call. |
| R10 | 6–8 | Writes only to /output | `/output` is the logical artifact root. Local runner maps it to `OUTPUT_DIR`/`./output`, identically for every owner. Native guard blocks common data writes outside it; Linux metadata limitations are disclosed. |
| R11 | 7 | Draft, check, fix; retain state | Checkpoints observe existing stages and copy artifacts. Missing/out-of-order/inconsistent evidence is flagged after exit; no feedback is sent to help the agent pass. Extra internal check/fix cycles may be recorded. |
| R12 | 5–6, 8–9 | Every run traced in Langfuse | Host evidence exported as one trace with model/search/phase observations, run ID and usage. Export status and trace URL retained; failed uploads replayable without rerunning. |
| R13 | 8 | Three scoring sources: script, model rubric and traces | Script verdict and infrastructure findings are separate. Model rubric remains explicitly pending until the quality owner completes grounding/factuality/readability review. |
| R14 | 6, 9 | Verifier only measures; no tuning to pass; smoke test without prompt changes | Verifier never edits agent artifacts or prompts or reruns trials. Record-only budgets observe; record-and-stop budgets implement common runtime guardrails. Team must choose this interpretation before freeze. |
| R15 | 10 | Bring verdicts, trace links, failure tags, setup effort and one learning | Reports/CSV plus owner handoff checklist in setup guide. No aggregate framework ranking is implied. |

## Source rules versus implementation choices

The PDF gives the rules above, not exact model IDs, resource ceilings, timeout values, retry configuration, caching policy, host specification or actual input files. None of the old pilot limits came from the PDF. [DECISIONS.md](DECISIONS.md) lists every unresolved setting.

The PDF lists five frameworks. Earlier team context also mentioned Pydantic AI. The final roster is therefore an explicit `framework_owners` decision instead of silently freezing either five or six.

The following are implementation choices, not verbatim presentation requirements: proxy as canonical usage source; OTLP export after run completion; artifact checkpoint API; local OUTPUT_DIR mapping; source-hash comparison groups; native write guards; missing-evidence exit codes; and the meaning of the proxy `steps` counter. Freeze these with the team along with the settings file.

## Observation, guardrails and verdicts

The verifier observes and scores after the attempt. It does not generate drafts, suggest fixes, modify outputs, search for a better prompt, or retry the whole task. An agent's own draft/check/fix behavior is part of the frozen task.

`budget_policy=record_and_stop` activates runtime watchdog stops using the shared limits. `record_only` records breaches without stopping; it can run until completion or operator interruption. Neither rewrites the upstream quality verdict. Stops are reactive: provider usage becomes known during/after a request, so an in-flight request can exceed a token or cost ceiling. These are measured watchdog limits, not provider billing quotas. Record-only is not a safe choice for an unbounded agent; choose the policy explicitly as a team.

The native write guard is an execution constraint applied uniformly. It can cause an agent to receive PermissionError; that is evidence, not an instruction to repair it. It does not count every denied write attempt. Linux Landlock content-write protection does not cover chmod/chown/timestamps/xattrs. For hostile workloads or full metadata isolation, use a reviewed read-only container deployment; this repo does not claim that capability.

`quality.verdict` refers only to the deterministic checker. `model_rubric.status=pending_external_review` is not a pass. Trace upload acceptance is not confirmation that the trace has been reviewed. The final slide-8/slide-10 result needs all three evidence sources.

## Freeze and failure policy

Preflight must pass before creating the bundle. Freeze resolves the banned-word placeholder once and copies prompt/feed/word-list/checker/config with hashes. Every scored launch checks these hashes and the verifier source hashes. A change requires a new bundle and experiment; do not combine the old and new trials.

Every attempt has a new run ID and a non-overwritable trial directory. Preserve a crashed, timed-out, over-budget or invalid-measurement attempt. Do not rerun it silently. If the team declares an infrastructure incident invalidates a trial, retain the original evidence and document the exception in a new experiment/run root.

Hard failures and soft warnings remain separate. The eight presentation tags are: misread_instruction, wrong_tool_or_args, lost_context, looped_or_quit_early, file_or_state_error, auth_error, said_done_but_failed, framework_limitation. Automatic tags cover observable checks; owners supply remaining diagnoses from traces, not from a fabricated automatic classifier.
