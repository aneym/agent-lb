# responses-api-compat-compaction Specification

## Purpose

Define how compaction turns and compaction output items cross the proxy. Entry capability: [responses-api-compat](../responses-api-compat/spec.md).

## Requirements

### Requirement: Bridge-routed remote-compaction turns carry compaction items in the terminal envelope

When a `/backend-api/codex/responses` request whose input contains a `compaction_trigger` item is served through the HTTP bridge (client HTTP mapped onto an upstream websocket session), the proxy MUST deliver a terminal `response.completed` event whose `response.output` array contains the compaction output items streamed for that request. When the upstream websocket terminal envelope carries an empty or missing `output` array, the proxy MUST re-inject the `compaction` items it observed in `response.output_item.done` events for the same request. A terminal envelope that already carries output items MUST pass through unmodified, and non-compaction turns MUST be unaffected.

#### Scenario: upstream websocket sends an empty terminal output array

- **GIVEN** a codex-native streaming request whose input includes a `compaction_trigger` item routed through the HTTP bridge
- **AND** upstream streams one `compaction` output item via `response.output_item.done` and then a `response.completed` envelope with `output: []`
- **WHEN** the proxy relays the terminal event downstream
- **THEN** the delivered `response.completed` envelope's `response.output` contains exactly that `compaction` item, including its `encrypted_content`

#### Scenario: upstream terminal envelope already carries output items

- **GIVEN** a bridge-routed compaction turn whose upstream `response.completed` envelope already lists output items
- **WHEN** the proxy relays the terminal event downstream
- **THEN** the envelope's `output` array is forwarded unmodified

#### Scenario: compaction trigger reaches upstream untouched

- **GIVEN** a codex-native request whose input includes a `compaction_trigger` item
- **WHEN** the bridge submits the request to the upstream websocket
- **THEN** the upstream payload's input still contains the `compaction_trigger` item

### Requirement: Compaction turns bypass the OpenAI-SDK public-contract normalization

A `/backend-api/codex/responses` request whose input contains a
`compaction_trigger` item MUST be treated as codex-native regardless of the
OpenAI-SDK shape heuristics (presence or absence of top-level `instructions`,
`Accept: text/event-stream`, or other shape signals). The proxy MUST NOT apply
the public-contract output-item normalization to such a turn, and the
`compaction` output items MUST reach the client unmodified on both the
streamed `response.output_item.*` events and the terminal `response.completed`
envelope.

#### Scenario: compaction turn without top-level instructions

- **GIVEN** a streaming request to `/backend-api/codex/responses` whose input
  includes a `compaction_trigger` item and whose payload has NO top-level
  `instructions` field
- **WHEN** upstream streams a `compaction` output item and completes
- **THEN** the client receives the `compaction` item and a terminal
  `response.completed` envelope containing it, and the stream MUST NOT be
  replaced by a `response.failed` event with code `invalid_output_item`

#### Scenario: compaction turn with top-level instructions keeps working

- **GIVEN** a streaming compaction turn that carries a top-level
  `instructions` string
- **WHEN** upstream streams a `compaction` output item and completes
- **THEN** the behavior is identical to the instructions-less case

#### Scenario: non-compaction SDK-shaped requests keep the public contract

- **GIVEN** a streaming request with `input` and no `instructions` whose input
  does NOT contain a `compaction_trigger` item
- **WHEN** the proxy serves it
- **THEN** the public-contract normalization applies exactly as before

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
