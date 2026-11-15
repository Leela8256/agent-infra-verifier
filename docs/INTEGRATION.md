# Shared services integration

## Owner contract

The default runner starts a separate loopback proxy and mock search service for every run. Owners only connect their model SDK and search tool to these endpoints. Do not add Recorder calls on this path.

- Model base URL: `INFRA_MODEL_BASE_URL` (already ends in `/v1`).
- Model and search authorization: `Authorization: Bearer $INFRA_PROXY_KEY`.
- Search endpoint: `INFRA_SEARCH_URL`.
- Optional request header: `X-Infra-Run-ID: $INFRA_RUN_ID`. An incorrect supplied ID is rejected. Omitting it is safe because the per-run credential and server bind identity.
- Artifacts: `OUTPUT_DIR` / `./output`. The working directory starts empty except for output.

For example, this shell fragment runs **inside the supervised agent process**:

```bash
curl --fail-with-body "$INFRA_SEARCH_URL?page=1" \
  -H "Authorization: Bearer $INFRA_PROXY_KEY"
# The first valid request intentionally returns 429; retry it, then fetch pages 2 and 3.
curl --fail-with-body "$INFRA_MODEL_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $INFRA_PROXY_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"your-frozen-model-id","messages":[{"role":"user","content":"your task"}]}'
```

The proxy also accepts `/responses`; both paths support streaming SSE and ordinary JSON. No other model routes or native Anthropic protocol are implemented. Select the compatible provider mode in Hermes/OpenClaw or any other framework. Environment variables alone do not configure SDKs that ignore them. Use absolute script/config/input paths because cwd changes; the runner resolves the executable and a direct script argument relative to the launch directory.

The search tool returns `results`, `page`, `total_pages: 3`, and `next_page` (null on page 3). GET `?page=N` and POST `{"page":N}` are equivalent. Feed order is frozen and split into exactly three consecutive slices. A query field is ignored: this is the common frozen search fixture, not live search. The first valid page request per run returns 429 and `Retry-After: 0`; malformed page requests return 400. Only successfully delivered 200 responses count toward coverage. The server logs each attempt, page, status, duration, and inferred retry relationship. This repository supplies the shared mock; the team must route every owner's search tool to it.

## Single source of truth

`usage_source=provider_usage_via_proxy` and `cost_source=provider_response_usage.cost`. The proxy captures prompt/input tokens, completion/output tokens, and the provider's usage.cost. Missing usage, including a failed request with no charge information, remains unknown; it is never treated as zero. A non-OpenRouter backend must return the same cost field to produce complete cost measurements. No price-table estimate or fallback Langfuse value is used.

Streaming usage snapshots replace previous snapshots for the same request, so repeated cumulative chunks are not added together. HTTP errors, embedded stream errors, and missing terminal markers are recorded. The proxy drains a disconnected client's upstream response to collect billed usage where possible. Provider response/request IDs and resolved model names are retained with the event. With frozen model enforcement (always on for scored sprint runs), bounded request/response text is retained for trace review, with truncation flags. General development mode without this option does not retain model text.

The supervisor forwards `X-Infra-Run-ID` upstream. It does not assume the upstream provider stores arbitrary headers. The local event log always binds the run ID. Model retries are inferred when the same endpoint and identical canonical JSON body follow a failed attempt. Search retries use the same page after failure. Changed-payload model retries cannot be reliably inferred and count as new attempts; retries are not a measure of internal SDK intentions.

`steps` is the number of model request attempts observed at the proxy, including retries; `steps_source=proxy_model_request_attempts`. Hidden reasoning steps are unavailable. Other tools not routed through the shared search service are outside tool-call coverage. Freeze tool availability accordingly.

## Credentials and provenance

Set the provider key in the parent environment (`OPENROUTER_API_KEY` by default; customize with `--model-key-env`). The parent injects it upstream. The real configured key is removed from the child's environment; `OPENAI_API_KEY` and `OPENROUTER_API_KEY` become local proxy credentials. A direct provider request using those local keys should fail. This does not prevent a trusted agent from reading another key from disk or using another network endpoint. Local mode is not an adversarial sandbox.

Loopback upstreams bypass system proxy discovery. Remote upstreams use explicit HTTP_PROXY/HTTPS_PROXY environment configuration; OS PAC auto-discovery is not used.

No agent-writable usage endpoint is exposed in shared mode. The separate /phase checkpoint endpoint accepts labels/findings and captures fixed artifact files; it cannot supply tokens or cost. Agent-supplied usage cannot override canonical fields. Events and final coverage are written by the services/supervisor. The existing Recorder interface is available only with explicit `--telemetry-source adapter_reported`, remains cooperative, and cannot be pooled with shared-service results.

## Experiment and Langfuse

Use the frozen `sprint-run` path described in [TEAM_SETUP.md](TEAM_SETUP.md) for scored attempts. It enforces requested model, temperature and per-request output-token limit, filling omitted values and rejecting conflicts with HTTP 409. Cache and SDK retry policy remain declarations. The returned resolved model is recorded separately. General `run --experiment` only enforces these settings when `--enforce-model-settings` is passed.

`--langfuse required` exports host evidence through OTLP/HTTP JSON after the run. The supervisor needs LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_PROJECT_ID; none are passed to the child. `langfuse-otlp.json` retains replayable payloads without authentication material; `langfuse-export.json` records accepted/failed status, partial ingestion and trace URL. Each span carries the run ID. Failed required export yields CLI exit 3 even if script quality passed. `export-langfuse RUN_DIR` retries upload without another agent attempt.

**Proxy usage/cost is canonical.** Langfuse receives those measurements for debugging and correlation; its inferred totals are never imported or merged. HTTP acceptance is not proof of live trace visibility. The operator must confirm one real smoke trace appears before freeze. Local tests use a fake OTLP receiver, not a live project.

The implementation follows [Langfuse OTLP documentation](https://langfuse.com/integrations/native/opentelemetry) and sends the version-4 ingestion header. It does not use the [retired legacy ingestion API](https://langfuse.com/self-hosting/upgrade/versioning).

## Workflow checkpoint contract

POST to INFRA_PHASE_URL with proxy authorization. Payloads: draft requires phase=draft; check requires phase=check and a findings array (empty means no issues); fix requires phase=fix and a nonempty resolution. The host copies existing blog.md and meta.json, with hashes, into phase-evidence. Files must be regular, nonsymlink artifacts, at most 1 MiB each. The response acknowledges receipt only.

After exit, the supervisor checks ordering, that check observed the preceding artifacts, that a fix changed at least one artifact when findings were reported, and that final artifacts match the last fix. Additional check/fix cycles are allowed. `workflow_phase_count` counts checkpoints; `steps` still counts model attempts. A change is evidence of a revision, not proof the finding was correctly fixed. Missing labels remain missing evidence and owner-declared labels cannot prove hidden reasoning.

Pass `--feed`, `--banned`, and `--reference-time` together for quality grading. `--search-feed` can override the search data, but use the same frozen feed for the sprint. Inputs are copied before launch. Quality exceptions produce unscored, never an invented failure.

## Workspace and outcome checks

Before launch the runner snapshots a fresh workspace, with artifacts in workspace/output. The persistent before/after audit catches file/directory/symlink changes outside output within that workspace. Audit-only mode cannot see machine-wide or transient writes.

Scored runs additionally enable native write guarding. macOS uses Seatbelt; Linux requires Landlock ABI >=3. A real probe verifies allowed output writes and denied outside writes before launching. Restrictions inherit to descendants and block ordinary content writes, removal, truncation and output-symlink escapes. They permit inherited stdout/stderr and /dev/null. Read and network access are not restricted. Linux chmod/chown/timestamps/xattrs remain unrestricted, as documented by [Landlock](https://docs.kernel.org/userspace-api/landlock.html); this is not a complete adversarial sandbox. Denied attempts are not automatically counted. Full metadata containment and network/tool allowlisting need a separate reviewed container deployment.

`said_done_but_failed` is exactly exit_code==0 and quality.verdict==fail. Partial/unscored/not_run verdicts do not trigger it. Search coverage and scope findings stay in `infra_checks`; they do not rewrite Mithilesh's quality verdict. Hard infrastructure findings cause CLI exit 2. `failure_tags` is hard-only; `warning_tags` is soft-only, with hard precedence when the same tag appears at both severities.
