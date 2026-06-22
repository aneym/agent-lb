## Why

Hermes and OpenAI SDK clients can receive a public `/v1/responses` stream that starts with `response.created` or `response.in_progress`, then dies with `stream_incomplete` / `upstream_stream_truncated` before any assistant output. The current retry layer treats those protocol-only events as already visible, so agent-lb forwards an empty failed turn instead of transparently retrying.

## What Changes

- Add pre-visible retry semantics for public OpenAI-contract Responses streams when the only emitted events are protocol lifecycle events and the attempt fails with a transient terminal error.
- Preserve the existing no-replay rule once user-visible output, tool-call output, or other non-lifecycle Responses data has been forwarded downstream.
- Keep Codex-internal `/backend-api/codex/*` streaming behavior unchanged.
- Cover the Hermes-style GPT failure with regression tests.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: public `/v1/responses` stream compatibility now includes transparent retry for transient protocol-only failed attempts before semantic output reaches the client.

## Impact

- Affected code: public Responses streaming routes and the proxy streaming retry layer.
- Affected APIs: streaming `POST /v1/responses` and equivalent public OpenAI SDK-compatible paths.
- Tests: integration coverage for protocol-only transient stream failure retry and unchanged backend no-replay behavior.
