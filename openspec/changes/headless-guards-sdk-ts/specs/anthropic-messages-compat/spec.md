## ADDED Requirements

### Requirement: Untagged headless Opus names a seat unless priority is high
When `refuse_untagged_headless_opus` is enabled, `/v1/messages` SHALL refuse an Opus request whose user agent contains `sdk-cli`, `sdk-ts`, or `agent-sdk/` and that carries no valid `x-agent-lb-seat`, with HTTP 403 and a permission error that names the seat header. The same request SHALL pass when it carries a valid seat, when `x-agent-lb-priority` stripped and lowercased is `high`, when the model is not Opus, or when the user agent is an interactive Claude Code client. The setting default SHALL remain disabled. While the setting is disabled, the proxy SHALL log that it would refuse an untagged headless Opus request and SHALL NOT emit that log when priority is `high`.

#### Scenario: Rails sdk-ts Opus without a seat is refused
- **GIVEN** `refuse_untagged_headless_opus` is enabled
- **WHEN** a client posts Opus to `/v1/messages` with user agent `claude-cli/2.1.288 (external, sdk-ts, agent-sdk/0.3.288)` and no seat
- **THEN** the response is HTTP 403 and names the untagged headless Opus rule

#### Scenario: Priority high is a person's session
- **GIVEN** `refuse_untagged_headless_opus` is enabled
- **WHEN** the same sdk-ts Opus request sets `x-agent-lb-priority` to `HIGH`
- **THEN** the proxy does not refuse it as untagged headless Opus

#### Scenario: Flag off still logs the would-refuse line
- **GIVEN** `refuse_untagged_headless_opus` is disabled
- **WHEN** an untagged sdk-ts Opus request arrives, and another arrives with priority `high`
- **THEN** the untagged request passes and the would-refuse log fires once
- **AND** the priority `high` request does not emit that log
