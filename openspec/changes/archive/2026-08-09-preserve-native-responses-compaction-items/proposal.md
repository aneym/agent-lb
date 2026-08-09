# Change: preserve native Responses compaction output items on the public surface

## Why

OpenAI's Responses API supports server-side compaction: a client sends
`context_management: [{"type": "compaction", "compact_threshold": N}]`, and once
the rendered input crosses N tokens the provider summarizes older context into an
opaque `compaction` output item carrying `encrypted_content`. Replaying that item
as an input item on later turns stands in for the pruned history. The blob is
sealed by the provider — a client can only obtain one by receiving it verbatim.

Agent LB already forwards `context_management` and `compaction` input items to
upstream unchanged, and the upstream provider does issue the item. A live probe
through the deployed proxy on 2026-08-09 (Codex-native route, verbatim
passthrough) returned a genuine provider-issued `compaction` output item on both
`response.output_item.added` and `response.output_item.done`, with a 1060-char
`encrypted_content` blob.

The public Responses surface then throws it away. `_normalize_public_output_item`
only passes through a fixed set of item types (plus `*_call` / `*_call_output`),
and everything else is coerced to a `message` by extracting text. A `compaction`
item has no text at all — only `encrypted_content` — so text extraction returns
nothing and the whole `response.output_item.*` event is dropped, with an
`invalid_output_item` contract violation recorded. Because the drop happens
before the item is collected for terminal backfill, the item is absent from the
streamed events *and* from the backfilled `response.completed` envelope, and the
non-streaming collect path drops it the same way.

Net effect: any OpenAI-SDK client pointed at Agent LB (Hermes GPT-5.6 native
compaction, among others) sends `context_management`, gets HTTP 200, and receives
zero compaction checkpoints — so it can never persist or replay one, and native
compaction is silently inert. Compaction output items are part of the OpenAI
Responses contract, so passing them through is what an OpenAI-compatible surface
is supposed to do.

## What Changes

- The public Responses surface (`/v1/responses` and `/backend-api/codex/responses`
  when the caller is detected as an OpenAI SDK client) forwards provider-issued
  `compaction` output items verbatim, preserving `encrypted_content`, instead of
  dropping them and recording a contract violation.
- Compaction items survive both the streaming path (`response.output_item.added`
  / `response.output_item.done`, and the terminal `response.completed` backfill)
  and the non-streaming collect path.
- Agent LB never synthesizes, rewrites, or re-seals a compaction item. Only items
  the provider actually issued are delivered.
- No other output item type changes behavior; clients that never send
  `context_management` never receive a compaction item, so existing traffic is
  unaffected.

## Superseded guard

`fix-direct-compaction-sdk-misclassification` (archived 2026-07-10) deliberately
kept `compaction` out of the public passthrough set, on the reasoning that "the
public /v1 OpenAI-SDK contract has no `compaction` output item type" — true when
compaction existed only as a Codex CLI vendor mechanism (`compaction_trigger`).
Native `context_management` compaction made it a first-class OpenAI Responses
output item, so that boundary is now wrong for the native path. The unit test
encoding it (`test_normalize_public_responses_stream_strips_compaction_item_under_sdk_contract`)
is replaced by one asserting preservation; the general "text-less unsupported
item is dropped" guard it also protected is kept with a non-compaction item type.
The Codex-native route's verbatim passthrough is unchanged.

## Impact

- Affected specs: `responses-api-compat`
- Affected code: `app/modules/proxy/api.py`
  (`_PUBLIC_RESPONSE_OUTPUT_ITEM_TYPES`)
