## ADDED Requirements

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
