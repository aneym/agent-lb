# Change: fix Codex remote-compaction v2 output passthrough

## Why

Codex CLI 0.135.0 ships "remote compaction v2". Unlike v1 (a JSON `POST
/responses/compact`), v2 streams a normal turn to `/backend-api/codex/responses`
with a `compaction_trigger` input item and expects the SSE response to carry
**exactly one** output item of type `compaction` (an opaque `encrypted_content`
blob with no text). OpenAI enables v2 per account via a managed feature flag
(`responses_compaction_v2`), so it rolled out gradually.

The proxy's public-stream normalizer (`_normalize_public_stream_payload`)
applied OpenAI-SDK output-item normalization **unconditionally** — even on the
Codex-native surface where `enforce_openai_sdk_contract=False`. Because
`compaction` is not on the SDK passthrough allowlist and has no extractable
text, the normalizer dropped the `response.output_item.done` compaction event
and turned the compaction-only `response.completed` into `response.failed`. The
Codex CLI then aborted with:

> remote compaction v2 expected exactly one compaction output item, got 0 from 0
> output items

This broke automatic and manual compaction for every Codex session routed to a
v2-enabled account, while v1 sessions kept working — matching the observed
intermittent failures.

## What changes

- On the Codex-native surface (`enforce_openai_sdk_contract=False`), forward
  `response.output_item.added`/`response.output_item.done` events and the
  `response.completed`/`response.incomplete` terminal envelopes **verbatim**,
  so native Codex item types (notably `compaction`) survive unaltered.
- Preserve the strict OpenAI-SDK contract normalization for the public
  `/v1` surface (`enforce_openai_sdk_contract=True`) unchanged.
- This aligns the implementation with the existing
  `_normalize_public_responses_stream` docstring, which already promises the
  Codex-native surface forwards events verbatim.

## Non-goals

- No change to remote-compaction v1 (`/responses/compact` JSON) handling.
- No change to the OpenAI-SDK `/v1` output-item contract or allowlist.
- No new configuration flags.
