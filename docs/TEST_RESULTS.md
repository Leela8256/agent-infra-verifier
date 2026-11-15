# Test results

Validated 2026-11-15T09:25:29-08:00 on macOS arm64; Python 3.14, psutil 7.2.2.

**77 automated tests passed.** Ruff lint/format checks passed. [Full test log](../test-results/unittest.log).

The shared-service tests use real HTTP transport and a fake upstream with synthetic tokens/cost. No paid model API, live Langfuse connection, or actual six-framework benchmark was run. Placeholder blog prose only tests structure.

## Populated sample: three frozen trials

The filled sample was resolved against local fake services, frozen with a hashed model-rubric file and run for trials 1, 2 and 3. All three completed with accepted mock OTLP uploads and explicitly pending model-rubric review. [Sample CSV](../test-results/frozen/summary.csv) · [Sample JSON](../test-results/frozen/summary.json). The checked-in sample stays draft. This demonstration is also run in every CI matrix job.

## Sprint protocol: seven retained end-to-end runs

| Scenario | Phase evidence | OTLP upload | CLI status |
|---|---|---|---:|
| [success](../test-results/sprint/success/report.json) | evidenced | accepted | 0 |
| [missing phases](../test-results/sprint/missing-phases/report.json) | missing | accepted | 3 |
| [wrong order](../test-results/sprint/wrong-order/report.json) | inconsistent | accepted | 2 |
| [unfixed findings](../test-results/sprint/unfixed-findings/report.json) | inconsistent | accepted | 2 |
| [denied outside write](../test-results/sprint/denied-outside-write/report.json) | evidenced | accepted | 0 |
| [partial upload](../test-results/sprint/partial-upload/report.json) | evidenced | failed | 3 |
| [authentication failure](../test-results/sprint/upload-auth-failure/report.json) | evidenced | failed | 3 |

[CSV](../test-results/sprint/summary.csv) · [JSON](../test-results/sprint/summary.json). Every retained sprint run used the native macOS write guard. The denied-write fixture catches PermissionError and completes; its zero exit confirms that no forbidden file was written, not that denied attempts are automatically tagged.

Additional automated tests cover frozen bundle creation, a scored launch, refusal to overwrite a trial, tampered inputs, unresolved settings, missing feed traps, model-setting conflicts, metadata-only fixes, extra review cycles, inherited child write restrictions, symlink escapes, and trace replay with stable IDs without rerunning the agent. Record-only budgets flag breaches without killing the process. All model-rubric results remain pending external review.

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

Audit-only mode inspects the fresh working tree before/after. Scored mode additionally uses native write restrictions; Linux Landlock excludes chmod/chown/timestamp/xattr changes and the guard does not log every denied write attempt. Read and network access, including direct provider bypass with other credentials, are not blocked. Provider keys are withheld from child environment but same-user local processes are trusted. The mock HTTP integration establishes transport/accounting behavior, not live OpenRouter compatibility or six-framework readiness. Each owner still needs a real-provider endpoint smoke test and the team must freeze the common feed and model/settings.

Linux/macOS CI is configured in GitHub Actions. Local evidence above is independent of the remote CI outcome.
