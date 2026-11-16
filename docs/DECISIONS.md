# Decisions required before scored runs

The presentation already fixes the task and main run rules. These values are not specified there and must not be guessed. Edit a copy of [sprint.template.json](../experiment/sprint.template.json); null means undecided. Preflight lists blockers and freezing refuses them. A fully populated **illustrative** [sample](../experiment/sprint.sample.json) and [sample model rubric](../experiment/model-rubric.sample.json) show the format; their values are not agreed defaults.

| Spec field | Decision needed | Responsible role |
|---|---|---|
| sprint_id, framework_owners | Experiment identifier, final five/six-framework roster, one owner each | Sprint lead |
| model, provider, model_upstream | Exact common model and compatible endpoint; OpenRouter URL is a proposed default | Sprint lead/model owner |
| temperature, max_tokens | Common inference settings; max_tokens is per request. Use `"omit"` for temperature on models that reject the parameter (Claude Opus 5.5 and Sonnet 5.5 accept only 1): the proxy leaves it out and returns 409 to any agent that sends one, so owners must turn their framework's default temperature off | Sprint lead |
| model_key_env | Name of the supervisor environment variable that holds the provider key (default `OPENROUTER_API_KEY`, e.g. `ANTHROPIC_API_KEY`) | Infrastructure owner |
| price_per_mtok | `{"input": USD, "output": USD}` per million tokens, for providers that return tokens but no cost (e.g. the Claude API). Cost = provider tokens × frozen price; a provider-reported cost still wins. Agree the price source and how cached/reasoning tokens are billed. `null` = provider cost only | Sprint lead + infrastructure owner |
| reasoning_effort | Optional frozen effort. `null` = not frozen: the agent's value passes through. When set, a different value gets 409. Anthropic's OpenAI-compatible endpoint ignores the field, so freezing it there has no effect | Sprint lead |
| phase_evidence | `declared_or_observed` (default): if no draft/check/fix checkpoints are declared, 2+ saved versions of blog.md count as revision evidence and 1 version is a soft finding. `declared`: explicit checkpoints required | Sprint lead |
| paths.verifier | Optional. Replaces Mithilesh's vendored checker in the bundle. Preflight warns, and every report records `quality_checker.is_upstream`. Confirm with the quality owner | Quality owner |
| cache_policy, sdk_retry_policy | Caching and SDK retry/backoff behavior, including how to handle the planted 429 | Owners agree together |
| request_timeout_s | Shared proxy request timeout | Infrastructure owner |
| paths.feed, feed_record_count | Actual frozen feed, approximately 30 posts, with all planted traps | Task/quality owner |
| paths.banned | Actual word list; template placeholder resolves once during freeze | Task/quality owner |
| reference_time | Fixed timestamp used for the seven-day freshness window | Task/quality owner |
| budgets.* | Wall time, CPU, memory, request counts, aggregate tokens, total cost and steps | Infrastructure owner + sprint lead |
| budget_policy | record_only or record_and_stop; document how this fits measurement-only evaluation and standard guardrails | Sprint lead |
| execution_host | Shared OS/architecture/CPU-count profile; operator ensures a common machine for resource comparisons | Infrastructure owner |
| langfuse_base_url, langfuse_project_id | Real project and region/self-hosted endpoint | Tracing owner |
| quality_review_protocol, optional paths.rubric | Exact external model-rubric procedure, grader identity/version, reviewer and score aggregation | Mithilesh/quality owner |
| status | Set ready only after decisions above are actually complete | Sprint lead |

For optional ceilings, use the literal string `disabled` rather than null. This implementation requires explicit wall-time, step and cost values for a frozen sprint. That is an operational choice based on the deck's guardrails, not a number supplied by the deck. `record_only` records those values without enforcing them.

`steps` means model request attempts, including retries; semantic phases have their own fields. The team must accept this shared definition or revise/freeze the implementation before the first scored attempt. Cache and SDK retry settings are declarations; the proxy cannot attest to provider-side caching or SDK internals.

No credentials go in this file. Keep OPENROUTER_API_KEY and LANGFUSE_PUBLIC_KEY/SECRET_KEY in the supervisor environment or secret manager. Framework owners receive only run-scoped local proxy credentials.

Additional team checks: live Langfuse trace visibility, actual provider cost field, tool allowlist, installed framework dependencies, shared output-path mapping, write-guard compatibility, and the full model-rubric scoring procedure. These are not made true by a passing offline demo.
