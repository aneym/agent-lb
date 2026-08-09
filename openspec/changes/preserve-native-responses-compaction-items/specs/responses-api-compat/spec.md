## ADDED Requirements

### Requirement: Public Responses surface preserves provider-issued compaction output items

When a client sends a Responses request carrying
`context_management: [{"type": "compaction", ...}]` and the upstream provider
issues a `compaction` output item, the proxy MUST deliver that item to the client
unchanged on the public OpenAI-SDK-contract surface, preserving its
`encrypted_content` byte-for-byte. The proxy MUST NOT coerce a `compaction` item
into a `message`, MUST NOT drop it, and MUST NOT record a public-contract
violation for it. The proxy MUST NOT synthesize, mint, re-seal, or otherwise
fabricate a compaction item that the provider did not issue.

Compaction items MUST survive both response shapes: the streaming path (the
`response.output_item.added` and `response.output_item.done` events, and the
terminal `response.completed` / `response.incomplete` envelope backfilled from
streamed output items) and the non-streaming collected response body.

Output item types other than `compaction` MUST keep their existing public-contract
handling.

#### Scenario: streamed compaction output item reaches an OpenAI SDK client

- **GIVEN** a streaming Responses request on the public surface
- **AND** upstream emits a `response.output_item.done` event whose item is
  `{"type": "compaction", "id": "...", "encrypted_content": "<blob>"}`
- **WHEN** the proxy normalizes the stream for the OpenAI SDK contract
- **THEN** the client receives a `response.output_item.done` event whose item is
  still of type `compaction` and whose `encrypted_content` equals `<blob>`
- **AND** no `invalid_output_item` contract violation is recorded

#### Scenario: terminal envelope carries the compaction item

- **GIVEN** a streaming Responses request on the public surface where upstream
  streams a `compaction` output item and then sends a terminal
  `response.completed` envelope with an empty `output` array
- **WHEN** the proxy backfills the terminal envelope from the streamed items
- **THEN** the delivered `response.completed` envelope's `response.output`
  contains the `compaction` item with its `encrypted_content` intact

#### Scenario: non-streaming response body carries the compaction item

- **GIVEN** a non-streaming Responses request on the public surface whose
  upstream stream contains a `compaction` output item alongside a `message` item
- **WHEN** the proxy collects the stream into a single response body
- **THEN** the returned body's `output` array contains both the `message` item
  and the `compaction` item with its `encrypted_content` intact

#### Scenario: compaction request fields and replayed checkpoints reach upstream

- **GIVEN** a Responses request carrying top-level `context_management` and an
  input item of type `compaction` with `encrypted_content`
- **WHEN** the proxy forwards the request upstream
- **THEN** the upstream payload still carries the top-level `context_management`
  value and the `compaction` input item with its `encrypted_content` unchanged

#### Scenario: unsupported non-compaction output items keep existing handling

- **GIVEN** a streaming Responses request on the public surface
- **AND** upstream emits an output item of an unsupported type that carries no
  text and is not a `compaction` item
- **WHEN** the proxy normalizes the stream for the OpenAI SDK contract
- **THEN** that item is still dropped and an `invalid_output_item` contract
  violation is still recorded
