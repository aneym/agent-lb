## ADDED Requirements

### Requirement: Bridged seats receive only their brief

The Claude-to-Responses bridge MUST NOT forward a Claude Code Workflow harness
relayed user request to the upstream model. A user turn or text part that opens
with the exact `[Workflow harness — user request]` frame header MUST be dropped.
A text that opens with the `[Workflow harness — computed task]` frame header and
whose first line ends with `The computed task text follows:` MUST have that first
line replaced by a frame naming the brief as the agent's only request; the brief
body MUST pass through unchanged. All other text MUST pass through unchanged.

#### Scenario: Lead chat moved on during a workflow run

- **GIVEN** a workflow subagent request whose first user turn is the relayed lead
  chat and whose next user turn is the computed task
- **WHEN** the bridge translates it to a Responses request
- **THEN** the upstream input contains no relayed chat text
- **AND** the brief body is present verbatim under the reframed header

#### Scenario: Ordinary text mentioning the frame

- **GIVEN** a user turn that does not open with a harness frame header line
- **WHEN** the bridge translates it
- **THEN** the text is forwarded unchanged
