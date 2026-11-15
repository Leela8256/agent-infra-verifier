# Infrastructure verification parameters

Owner: Leela. Infrastructure measurements accompany the upstream quality verdict.

## Resources

| Field | Unit / source | Meaning |
|---|---|---|
| wall_clock_s | Monotonic seconds, supervisor | Launch through detected exit/reaping; includes startup and API waits, excludes grading |
| cpu_time_s | CPU-seconds | Max of summed sampled process CPU and OS wait4 CPU |
| peak_rss_mb | MiB, psutil | Maximum sampled simultaneous RSS sum of observed live processes |
| reaped_cpu_time_s | CPU-seconds, wait4 | Leader and descendants included by OS child-accounting semantics |
| reaped_peak_rss_mb | MiB, wait4 | OS process high-water measure; not a simultaneous tree peak |
| memory_observed_mb | MiB | Max of sampled RSS peak and reaped high-water RSS, for retrospective budget comparison |
| orphaned_processes | Count | Observed children left after leader exit; killed and flagged |
| hit_budget_cap | Boolean | Watchdog actively stopped the process |
| sample_count | Count | Samples retained; interval is frozen in configuration |

MB field names match upstream; units are MiB (1,048,576 bytes). Darwin wait4 RSS is bytes; Linux is KiB. Tree RSS can double-count shared memory and sampling can miss peaks. Do not pool this backend with container charged-memory results.

## Events

| Field | Meaning |
|---|---|
| tokens_in / tokens_out | Provider usage reported by adapter; null if any attempt's value or coverage is missing |
| cost_usd | Reported billed/calculated cost, including retries; null if any amount is unknown |
| llm_calls / tool_calls | Attempt counts; tool_calls_by_name preserves the split |
| retries | Attempts linked to a prior failed attempt with retry_of |
| errors / errors_by_type | Failed attempts, including recovered errors |
| llm_latency_s | Client-observed model request durations, not provider GPU compute |
| llm_total_request_s / tool_total_request_s | Summed durations; may exceed wall time for concurrent calls |
| time_to_first_tool_call_s | First reported tool start, relative to runner monotonic origin |
| steps | Explicit semantic steps; define the same boundaries in every adapter |
| retry_recovered | A failed tool call has a successful linked retry; not a claim of final task success |
| usage_known_subtotal | Partial known amounts for interrupted traces; not complete totals |

Cached input and reasoning output may be subsets of totals. Do not add them again. Supply billed cost or a calculation with frozen prices and non-overlapping categories. This implementation does not guess model prices or substitute visible-output tokenization for billed usage.

Identical duplicate event IDs are idempotent. Conflicting duplicates, malformed events, and reported model mismatch invalidate measurements.

## Budgets and validity

Config fields: wall_clock_s, cpu_time_s, memory_mb, model_calls, tool_calls, tokens_in, tokens_out, cost_usd, steps. Null disables a ceiling. Strictly exceeding it breaches the budget; equality is allowed. Unknown keys, zero/negative limits, booleans, fractional counts, NaN and infinity are rejected.

The watchdog checks each sample cycle. Limits are reactive, not kernel ceilings or pre-request reservations. The report distinguishes an active stop from a retrospective observed breach.

Budget status:
- exceeded: at least one observed breach.
- within_observed_limits: complete declared coverage, no collector errors, no observed breach.
- unknown: insufficient measurement for a complete conclusion.

Measurement status:
- complete: collection succeeded and required coverage was declared.
- incomplete: coverage or usage missing.
- invalid: collector/supervisor error, invalid event, or model mismatch.

require_telemetry=false permits resource-only measurement completeness, while budget status remains unknown for unobserved model/tool usage. Missing values never become zero.

## Comparison controls and derived metrics

Experiment JSON declares model/provider/temperature/max_tokens/cache_policy. Its hash joins the feed, banned words, checker, this verifier's source, dependency version, budgets, reference time, and recorded OS/hardware profile in a comparison key.

This partitions incompatible runs; it cannot prove actual SDK settings, physical hardware performance, host load, provider routing, caching, or network conditions.

Cost per passing run = total cost across all attempts / quality-passing completed runs. It is null when cost is incomplete or there are no passing runs. Token variability uses sample standard deviation. Time summaries use completed runs and report timeout counts separately.

Record setup_minutes, dependencies added, lines to the working agent, and tracing effort manually: these measure human setup work.

## Basis

- [Mithilesh's metric contract](https://github.com/mithileshgau/agent-blog-verifier/blob/6630669c083fcac811b04acdb6df644ff54a490c/metrics.md)
- [Python resource accounting](https://docs.python.org/3/library/resource.html)
- [psutil](https://psutil.readthedocs.io/)
- [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html) for the future container backend
- [Langfuse usage and costs](https://langfuse.com/docs/observability/features/token-and-cost-tracking)
- [AI Agents That Matter](https://arxiv.org/abs/2407.01502)
- [Infrastructure noise](https://www.anthropic.com/research/infrastructure-noise)
