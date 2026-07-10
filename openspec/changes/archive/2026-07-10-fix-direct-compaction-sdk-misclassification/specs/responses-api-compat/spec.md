## ADDED Requirements

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
