## ADDED Requirements

### Requirement: Codex-native responses stream forwards output items verbatim

On the Codex-native streaming surface (`/backend-api/codex/responses` and any responses stream processed with `enforce_openai_sdk_contract=False`), the proxy MUST forward upstream `response.output_item.added`, `response.output_item.done`, `response.completed`, and `response.incomplete` events verbatim — including output items whose `type` is not on the public OpenAI-SDK allowlist (for example the `compaction` item produced by Codex remote-compaction v2, which carries `encrypted_content` and no text) — and MUST NOT drop, re-type, or substitute a contract-failure event for such items on this surface.

The strict OpenAI-SDK output-item normalization MUST continue to apply on the public `/v1` surface (`enforce_openai_sdk_contract=True`), where unsupported output item types are normalized or rejected.

#### Scenario: Codex remote-compaction v2 item is forwarded unaltered

- **WHEN** the upstream emits a `response.output_item.done` with
  `item.type = "compaction"` (carrying `encrypted_content`, no text) followed by
  a `response.completed` whose `output` contains that item, on a request with
  `enforce_openai_sdk_contract=False`
- **THEN** the proxy forwards the `response.output_item.done` event with the
  `compaction` item unchanged
- **AND** forwards the `response.completed` event with the `compaction` item in
  `response.output`
- **AND** does not emit a `response.failed` event for the compaction-only turn

#### Scenario: Public /v1 surface still normalizes unsupported items

- **WHEN** the same compaction-only stream is processed with
  `enforce_openai_sdk_contract=True`
- **THEN** the unsupported `compaction` output item is dropped
- **AND** the compaction-only terminal becomes a contract-failure event rather
  than leaking a non-standard item to OpenAI-SDK consumers
