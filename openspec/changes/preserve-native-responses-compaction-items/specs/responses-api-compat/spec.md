## ADDED Requirements

### Requirement: Public Responses preserve native compaction checkpoints

The public Responses API MUST pass provider-issued `compaction` output items through unchanged, including their opaque `encrypted_content` and any additional fields. Such items MUST NOT require plain text, be relabeled as messages, or cause an `invalid_output_item` contract violation.

#### Scenario: Streaming checkpoint item events

- **WHEN** upstream emits a `compaction` item in `response.output_item.added` or `response.output_item.done`
- **THEN** the public stream MUST preserve the item unchanged in the corresponding event
- **AND** normalization MUST NOT report an `invalid_output_item` violation for that item

#### Scenario: Terminal response contains a checkpoint

- **WHEN** upstream supplies a `compaction` item in a terminal response's output array
- **THEN** the public terminal response MUST retain the item unchanged

#### Scenario: Terminal output needs backfilling

- **WHEN** upstream streams a `compaction` item and omits it from the terminal response's output array
- **THEN** the public terminal response MUST include the collected checkpoint unchanged through the existing output backfill behavior

#### Scenario: Non-streaming collected response

- **WHEN** a client requests a non-streaming response and upstream emits a `compaction` item
- **THEN** the collected response's output array MUST preserve the checkpoint unchanged
