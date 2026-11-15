# Instrument an actual agent

## Python adapter

Instrument the provider SDK and actual tool execution boundaries once per attempt. The example variable names below are placeholders for real response data.

~~~python
import os
import time
from pathlib import Path
from infra_verifier.sdk import Recorder

recorder = Recorder()
output = Path(os.environ["OUTPUT_DIR"])

started = time.monotonic()
# response = actual provider SDK call
recorder.call(
    "model",
    "exact-provider-model-id",
    started,
    tokens_in=actual_input_tokens,
    tokens_out=actual_output_tokens,
    cost_usd=actual_cost_or_none,
)
recorder.step("draft")
# Save actual artifacts, check and fix as required.
recorder.finish()
~~~

Use None for unavailable usage/cost. Never substitute zero. For failed attempts, send status="error" and an error_type such as "http_429"; retain billed usage when known. A retry's retry_of is the ID returned by the previous failed call.

Do not emit both aggregate agent usage and leaf-call usage. Call finish after all asynchronous calls and their telemetry have completed. A crash or budget stop leaves a preserved partial trace.

## HTTP interface

POST JSON to INFRA_EVENT_URL with Authorization: Bearer followed by INFRA_EVENT_TOKEN.

Common fields: run_id=INFRA_RUN_ID, unique event_id, kind=model/tool/step/finish.

Model/tool events require name, status (ok/error), duration_s, start_offset_s. Offset uses the same local monotonic clock domain as INFRA_STARTED_MONOTONIC. Model events carry tokens_in, tokens_out, cost_usd, with null for unavailable values. Optional retry_of references a failed same-kind/same-name attempt; error_type is a string.

Step events count agreed semantic steps. Finish contains coverage=["model","tool","step"], including only genuinely instrumented event kinds.

HTTP 204 acknowledges acceptance or an identical retransmission. HTTP 400 rejects invalid telemetry and records a measurement error; HTTP 401 rejects unauthenticated requests. Reuse an event ID only for an identical retransmission.

This is cooperative instrumentation. The agent receives the token and can submit incorrect data. Provider-attested or adversarial evaluations need an external model/tool gateway.

## Frozen experiment

Pass a JSON file with --experiment containing model, provider, temperature, max_tokens and cache_policy. The shipped example is explicitly synthetic. Match the model event name to the declared model. Configure the real SDK consistently; only reported identity is checked here.

## Quality and Langfuse

Pass --feed, --banned and --reference-time together. The runner copies inputs before launch and calls the original checker after measurement. Checker exceptions produce unscored, never an invented quality failure. The bundled integration runs the deterministic tier; the LLM rubric remains separate.

Keep existing Langfuse framework tracing. Correlate with INFRA_RUN_ID and supply --trace-url when known before launch. When it is only known afterwards, keep a separate run_id-to-URL mapping instead of silently rewriting frozen evidence.

No live Langfuse upload, trace download, or credential handling is implemented. A live exporter needs a separate test against the team's project.
