# Test results

Validated 2026-11-15T08:36:46-08:00 on macOS arm64; Python 3.14, psutil 7.2.2.

**62 automated tests passed.** Ruff lint/format checks passed. [Full test log](../test-results/unittest.log).

The shared-service tests use real HTTP transport and a fake upstream with synthetic tokens/cost. No paid model API, live Langfuse connection, or actual six-framework benchmark was run. Placeholder blog prose only tests structure.

## Shared services: eight retained runs

| Scenario | Runtime | Quality | Measurement | Pages | Hard tags |
|---|---|---|---|---|---|
| [success](../test-results/shared/success/report.json) | completed | pass | complete | [1, 2, 3] | none |
| [stream](../test-results/shared/stream/report.json) | completed | pass | complete | [1, 2, 3] | none |
| [responses](../test-results/shared/responses/report.json) | completed | pass | complete | [1, 2, 3] | none |
| [responses-stream](../test-results/shared/responses-stream/report.json) | completed | pass | complete | [1, 2, 3] | none |
| [missing-page](../test-results/shared/missing-page/report.json) | completed | pass | complete | [1, 2] | looped_or_quit_early |
| [outside-write](../test-results/shared/outside-write/report.json) | completed | pass | complete | [1, 2, 3] | file_or_state_error |
| [false-done](../test-results/shared/false-done/report.json) | completed | fail | complete | [1, 2, 3] | file_or_state_error, said_done_but_failed |
| [token-budget](../test-results/shared/token-budget/report.json) | budget_exceeded | fail | complete | [1, 2, 3] | file_or_state_error |

[Shared CSV](../test-results/shared/summary.csv) · [Shared JSON](../test-results/shared/summary.json)

The first four cases receive 1,200 input tokens, 700 output tokens and $0.004 from the fake upstream. The proxy observes one model call/step and four search attempts: planted 429, successful page-1 retry, page 2, page 3. Outside-write and missing-page retain the original quality pass while failing separate infrastructure checks. False-done has exit 0 with quality fail. The token-budget case stops at a 1,000-token threshold.

## Legacy regression demonstration

All 13 original scenarios were rerun against this version using explicit adapter_reported mode. [Legacy CSV](../test-results/demo/summary.csv) and [JSON](../test-results/demo/summary.json) preserve real process metrics and synthetic adapter records. Shared and adapter runs remain separate comparison groups.

## Coverage

- Model proxy captures ordinary JSON and SSE for Chat Completions and Responses. Repeated usage is not summed twice.
- Model HTTP retry, unknown error cost, missing/invalid usage, stream errors/truncation, and upstream redirect rejection.
- Loopback servers avoid reverse-DNS startup lookups, and local upstreams bypass system proxy discovery. Both have regression coverage.
- Per-run authentication, run-ID mismatch, independent planted 429s, no agent-writable usage endpoint, and provider-key removal from the child environment.
- Shell/curl client through the actual CLI: no Python Recorder instrumentation.
- All-three-page coverage, repeated-page rejection, and search retry recovery.
- Fresh-cwd persistent create/modify/delete/symlink checks, allowed output files, and exit-0/quality-fail detection.
- All requested CSV fields and separate hard/soft tags.
- Existing timeout/CPU/memory/token limits, child cleanup, original checker hash, unknown usage, and comparison grouping.

## Limits

Filesystem checks only inspect the fresh working tree before/after, and do not provide containment or machine-wide write auditing. Direct network/provider bypass is not blocked. Provider keys are withheld from child environment but same-user local processes are trusted. The mock HTTP integration establishes transport/accounting behavior, not live OpenRouter compatibility or six-framework readiness. Each owner still needs a real-provider endpoint smoke test and the team must freeze the common feed and model/settings.

Linux/macOS CI is configured in GitHub Actions. Local evidence above is independent of the remote CI outcome.
