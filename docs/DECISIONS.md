# Decisions required before scored runs

The presentation already fixes the task and main run rules. These values are not specified there and must not be guessed. Edit a copy of [sprint.template.json](../experiment/sprint.template.json); null means undecided. Preflight lists blockers and freezing refuses them. A fully populated **illustrative** [sample](../experiment/sprint.sample.json) and [sample model rubric](../experiment/model-rubric.sample.json) show the format; their values are not agreed defaults.

| Spec field | Decision needed | Responsible role |
|---|---|---|
| sprint_id, framework_owners | Experiment identifier, final five/six-framework roster, one owner each | Sprint lead |
| model, provider, model_upstream | Exact common model and compatible endpoint; OpenRouter URL is a proposed default | Sprint lead/model owner |
| temperature, max_tokens | Common inference settings; max_tokens is per request | Sprint lead |
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
