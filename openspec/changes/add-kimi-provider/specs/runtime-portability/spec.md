## ADDED Requirements

### Requirement: Local Kimi launcher uses agent-lb and preserves the seat lineup
The local `kimi` shell launcher SHALL point Claude Code at the local agent-lb Anthropic-compatible base URL instead of directly at Moonshot. It SHALL remap only the driver model and the opus/fable default slots to a Kimi model, SHALL NOT override the sonnet, haiku, or generic subagent model slots, and SHALL use only a downstream proxy credential or local placeholder as `ANTHROPIC_AUTH_TOKEN`; real Kimi credentials SHALL come from the stored Kimi account inside agent-lb.

#### Scenario: Kimi command routes through local agent-lb
- **WHEN** an operator starts Claude Code with the `kimi` shell command
- **THEN** Claude Code sends Anthropic-compatible Messages requests to agent-lb
- **AND** the driver model is a Kimi model
- **AND** no Moonshot or Kimi credential appears in the launcher environment

#### Scenario: Loopback traffic bypasses the outbound egress proxy
- **GIVEN** the environment sets an outbound `HTTPS_PROXY`/`https_proxy` for upstream egress
- **WHEN** an operator starts Claude Code with the `kimi` shell command
- **THEN** the launcher excludes loopback hosts from proxying
- **AND** requests reach the local agent-lb instead of failing before any request is sent

#### Scenario: Canonical seats survive kimi mode
- **GIVEN** Claude Code is running via the `kimi` launcher
- **WHEN** a subagent seat pinned to a sonnet or sol-alias model is dispatched
- **THEN** the request reaches agent-lb with the seat's own model unchanged
- **AND** agent-lb routes it to that model's own provider pool
