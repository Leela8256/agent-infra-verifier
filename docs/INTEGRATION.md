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

Streaming usage snapshots replace previous snapshots for the same request, so repeated cumulative chunks are not added together. HTTP errors, embedded stream errors, and missing terminal markers are recorded. The proxy drains a disconnected client's upstream response to collect billed usage where possible. Provider response/request IDs and resolved model names are retained with the event. No prompt or completion text is written to the event log.

The supervisor forwards `X-Infra-Run-ID` upstream. It does not assume the upstream provider stores arbitrary headers. The local event log always binds the run ID. Model retries are inferred when the same endpoint and identical canonical JSON body follow a failed attempt. Search retries use the same page after failure. Changed-payload model retries cannot be reliably inferred and count as new attempts; retries are not a measure of internal SDK intentions.

`steps` is the number of model request attempts observed at the proxy, including retries; `steps_source=proxy_model_request_attempts`. Hidden reasoning steps are unavailable. Other tools not routed through the shared search service are outside tool-call coverage. Freeze tool availability accordingly.

## Credentials and provenance

Set the provider key in the parent environment (`OPENROUTER_API_KEY` by default; customize with `--model-key-env`). The parent injects it upstream. The real configured key is removed from the child's environment; `OPENAI_API_KEY` and `OPENROUTER_API_KEY` become local proxy credentials. A direct provider request using those local keys should fail. This does not prevent a trusted agent from reading another key from disk or using another network endpoint. Local mode is not an adversarial sandbox.

No writable telemetry endpoint is exposed in shared mode. Agent-supplied usage cannot override canonical fields. Events and final coverage are written by the services/supervisor. The existing Recorder interface is available only with explicit `--telemetry-source adapter_reported`, remains cooperative, and cannot be pooled with shared-service results.

## Experiment and Langfuse

Pass `--experiment` with model/provider/temperature/max_tokens/cache_policy. Hashes partition comparisons. Configure all SDK settings consistently; the requested model identity is checked, while other settings remain declarations. The proxy records a returned resolved model separately.

Keep Langfuse traces tagged with `INFRA_RUN_ID`; optionally pass `--trace-url`. **Use proxy totals in the comparison sheet.** Langfuse is for debugging and correlation; its token/cost totals are not imported or merged. Live export/import is not implemented.

Pass `--feed`, `--banned`, and `--reference-time` together for quality grading. `--search-feed` can override the search data, but use the same frozen feed for the sprint. Inputs are copied before launch. Quality exceptions produce unscored, never an invented failure.

## Workspace and outcome checks

Before launching, the runner snapshots a fresh `workspace/`, with artifacts in `workspace/output/`. After process cleanup it compares persistent file hashes, directory entries, and symlink targets outside output. This catches create/modify/delete changes within the working tree. It does not monitor absolute/parent-path writes elsewhere, changes through output symlinks, or create-then-delete activity. `workspace-audit.json` includes the scope and limitations. Use a container for real write containment.

`said_done_but_failed` is exactly exit_code==0 and quality.verdict==fail. Partial/unscored/not_run verdicts do not trigger it. Search coverage and scope findings stay in `infra_checks`; they do not rewrite Mithilesh's quality verdict. Hard infrastructure findings cause CLI exit 2. `failure_tags` is hard-only; `warning_tags` is soft-only, with hard precedence when the same tag appears at both severities.
