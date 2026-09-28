## ADDED Requirements

### Requirement: The Sonnet alias resolves to the newest Sonnet

The coding-agent policy install MUST pin Claude Code's `sonnet` alias to `claude-sonnet-5-5` through `ANTHROPIC_DEFAULT_SONNET_MODEL` in the user's Claude Code settings, and MUST treat `claude-sonnet-5` as retired.

#### Scenario: Install pins the alias

- **WHEN** install-policy reconciles `~/.claude/settings.json`
- **THEN** `env.ANTHROPIC_DEFAULT_SONNET_MODEL` equals `claude-sonnet-5-5`
- **AND** other `env` entries are kept

#### Scenario: Uninstall removes only its own pin

- **WHEN** install-policy runs with `--uninstall` and the setting still equals `claude-sonnet-5-5`
- **THEN** the setting is removed, and an empty `env` object is removed with it

#### Scenario: An explicit old Sonnet pin is refused

- **WHEN** a seat or subagent prompt pins `claude-sonnet-5`
- **THEN** the seat guard denies it as a retired model
