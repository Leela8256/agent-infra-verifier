# Test results

Validated 2026-11-15 05:30 PST on Darwin arm64, Python 3.14.7, psutil 7.2.2.

**37 automated tests passed.** Ruff lint and format checks passed. Tests exercise real subprocesses, CPU work, memory allocations, process cleanup, the HTTP event receiver, and the original quality checker. The full log is [unittest.log](../test-results/unittest.log).

**Model/tool events, token counts, and dollar costs are synthetic fixtures. No paid model API was called. The success fixture is mechanically valid placeholder prose, not a grounded blog.**

## Demonstration: 13 runs

| Scenario | Runtime | Quality | Measurement | Wall seconds | Sampled tree RSS MiB | CPU seconds |
|---|---|---|---|---:|---:|---:|
| [success](../test-results/demo/success/report.json) | completed | pass | complete | 0.130 | 31.53 | 0.052 |
| [retry](../test-results/demo/retry/report.json) | completed | pass | complete | 0.198 | 31.27 | 0.054 |
| [missing-output](../test-results/demo/missing-output/report.json) | completed | fail | complete | 0.133 | 31.41 | 0.056 |
| [timeout](../test-results/demo/timeout/report.json) | budget_exceeded | fail | incomplete | 0.413 | 27.67 | 0.039 |
| [memory](../test-results/demo/memory/report.json) | budget_exceeded | fail | incomplete | 0.078 | 123.50 | 0.046 |
| [child-memory](../test-results/demo/child-memory/report.json) | budget_exceeded | fail | incomplete | 0.075 | 139.11 | 0.057 |
| [cpu](../test-results/demo/cpu/report.json) | budget_exceeded | fail | incomplete | 0.258 | 27.44 | 0.255 |
| [crash](../test-results/demo/crash/report.json) | agent_error | fail | incomplete | 0.064 | 10.88 | 0.044 |
| [missing-telemetry](../test-results/demo/missing-telemetry/report.json) | completed | pass | incomplete | 0.069 | 9.33 | 0.042 |
| [token-budget](../test-results/demo/token-budget/report.json) | budget_exceeded | fail | incomplete | 0.146 | 31.34 | 0.047 |
| [orphan](../test-results/demo/orphan/report.json) | orphaned_processes | fail | incomplete | 0.366 | 138.95 | 0.059 |
| [malformed-event](../test-results/demo/malformed-event/report.json) | completed | fail | invalid | 0.069 | 10.66 | 0.051 |
| [unknown-usage](../test-results/demo/unknown-usage/report.json) | completed | pass | incomplete | 0.135 | 31.42 | 0.052 |

The timeout profile used 0.4 seconds. Memory profiles used a 50 MiB observational ceiling; the deliberately large allocation overshot before the watchdog killed it. The CPU profile used 0.2 CPU-seconds. The token profile used 2,000 input tokens and received a synthetic 9,000-token record. These are failure-injection settings, not proposed production limits.

Each attempt includes manifest.json, samples.jsonl, events.jsonl, agent logs, copied grading inputs, quality.json and report.json. [CSV comparison](../test-results/demo/summary.csv) and [JSON comparison](../test-results/demo/summary.json) preserve all attempts.

## What the automated tests establish

- Correct runs retain the upstream pass; missing outputs retain upstream fail.
- Timeout, CPU, memory and token overruns trigger watchdog stops.
- Memory allocated by a child is observed; observed leftover children are cleaned up.
- Crashes remain agent errors; a broken grader becomes unscored.
- Missing token/cost records remain null; malformed records invalidate measurement.
- Duplicate event delivery does not double-count usage; conflicting duplicates are rejected.
- Retry recovery remains visible with the original error.
- Model mismatch invalidates the measurement; model/budget/input/source changes partition comparison groups.
- Failed-attempt costs are included; unknown cost and zero successes cannot produce misleading cheap results.
- Original upstream verify.py Git blob hash matches exactly.

## Untested or not implemented

Container isolation/cgroup accounting, real-provider usage, live Langfuse export, actual six-framework runs, GPU/network/disk metrics, and LLM rubric grading were not tested. Docker was installed but its engine did not respond. Only the local sampled backend is implemented. The GitHub Actions workflow is provided for Linux/macOS but has not been run remotely.

Local measurements are reactive and sampling-based, with the caveats in [METRICS.md](../METRICS.md). These observations validate the verifier machinery, not comparative framework performance.
