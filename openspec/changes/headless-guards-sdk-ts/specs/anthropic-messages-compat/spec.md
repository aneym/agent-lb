## ADDED Requirements

### Requirement: Untagged headless Opus is logged, never refused
`/v1/messages` SHALL serve an Opus request whose user agent contains `sdk-cli`, `sdk-ts`, or `agent-sdk/` and that carries no valid `x-agent-lb-seat`, and SHALL log the warning `untagged_headless_opus` with the model and client session. The proxy SHALL NOT emit that warning when the request carries a valid seat, when `x-agent-lb-priority` stripped and lowercased is `high`, when the model is not Opus, or when the user agent is an interactive Claude Code client.

#### Scenario: Rails sdk-ts Opus without a seat runs and warns
- **WHEN** a client posts Opus to `/v1/messages` with user agent `claude-cli/2.1.288 (external, sdk-ts, agent-sdk/0.3.288)` and no seat
- **THEN** the request is served and the `untagged_headless_opus` warning fires once

#### Scenario: Priority high is a person's session
- **WHEN** the same sdk-ts Opus request sets `x-agent-lb-priority` to `HIGH`
- **THEN** the request is served and no `untagged_headless_opus` warning fires
