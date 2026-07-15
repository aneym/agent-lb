## MODIFIED Requirements

### Requirement: Claude Code harness execution
The system SHALL provide a `ccgpt` command that executes the installed Claude Code harness while routing inference through agent-lb's dedicated Codex compatibility path, launching with permission mode `bypassPermissions` unless the operator explicitly supplies a permission option.

#### Scenario: Interactive launch
- **WHEN** an operator runs `ccgpt`
- **THEN** the real Claude Code interactive harness starts with the compatibility route enabled and permission mode `bypassPermissions`

#### Scenario: Headless launch
- **WHEN** an operator runs `ccgpt -p <prompt>`
- **THEN** Claude Code completes the headless turn through the compatibility route with permission mode `bypassPermissions`

#### Scenario: Explicit permission option wins
- **WHEN** an operator runs `ccgpt` with `--permission-mode <mode>` or `--dangerously-skip-permissions`
- **THEN** the launcher injects no permission mode of its own and the operator's option applies
