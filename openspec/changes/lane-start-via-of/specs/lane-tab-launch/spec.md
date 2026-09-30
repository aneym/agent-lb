## ADDED Requirements

### Requirement: Installed rollout controls interactive herdr tags
The launcher SHALL infer tags only when AGENT_LB_INTENT is unset or empty, HERDR_TAB_ID is present, and the invocation is neither print mode nor a child Claude process. It SHALL read the managed installed routing table first, falling back to `~/.agents/policy/coding-agents/routing-table.json`; an explicit ROUTE_TABLE SHALL override both. Missing stand_in_rollout policy and stage off SHALL leave inferred tags absent. Stage canary SHALL select listed tabs or registry lanes. Stage all SHALL select registered and unregistered herdr tabs. Registry kinds ephemeral and background SHALL remain excluded. Selected orchestrator-kind tabs SHALL receive intent orchestrator; other selected tabs SHALL receive lane-tab. The launcher SHALL set an absent or empty AGENT_LB_LANE to the registry lane, falling back to the tab ID.

#### Scenario: Canary selects one build lane
- **GIVEN** canary lists tab w5H:tC8 and its registry lane is build
- **WHEN** an interactive launch starts in that tab without explicit intent
- **THEN** intent is lane-tab and lane is build
- **AND** an unlisted tab with an unlisted registry lane remains untagged

#### Scenario: All includes unregistered tabs but excludes disposable tabs
- **GIVEN** rollout stage is all
- **WHEN** unregistered, orchestrator, ephemeral, and background tabs launch
- **THEN** the unregistered tab receives lane-tab with its tab ID as lane
- **AND** the orchestrator receives orchestrator intent
- **AND** ephemeral and background tabs receive no inferred tags

#### Scenario: Explicit intent and headless launches stay unchanged
- **GIVEN** rollout stage is all
- **WHEN** a launcher has explicit intent or is invoked in print mode or as a child Claude process
- **THEN** explicit intent wins
- **AND** print and child invocations receive no inferred tags

### Requirement: Tag lookup fails open within a bounded budget
The launcher SHALL perform policy resolution without subprocesses and SHALL stop waiting within 50 milliseconds. Any read or parse failure SHALL leave inferred tags absent without failing launch. A missing registry SHALL count as an unregistered tab. Resolved tags SHALL be captured by the proxy and removed from the Claude child environment. Dry-run output SHALL show resolved intent, lane, and the reason for resolution or absence.

#### Scenario: Malformed policy or registry does not prevent launch
- **GIVEN** the installed table or a present registry file is malformed
- **WHEN** the launcher runs in dry-run mode
- **THEN** inferred intent and lane are absent
- **AND** output reports the lookup failure and the launch command
- **AND** the launcher exits successfully
