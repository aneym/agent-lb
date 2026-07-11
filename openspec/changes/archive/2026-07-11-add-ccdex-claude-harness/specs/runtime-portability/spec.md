## ADDED Requirements

### Requirement: Portable ccdex command
Supported local installations SHALL expose an executable `ccdex` command or documented shell function backed by the repository launcher, without hardcoding secrets.

#### Scenario: Installed command
- **WHEN** agent-lb is installed and Claude Code is present
- **THEN** `ccdex --version` or a dry run resolves the real Claude binary and the local compatibility capability

#### Scenario: Missing Claude Code
- **WHEN** the Claude binary cannot be resolved
- **THEN** `ccdex` fails with a clear nonzero diagnostic
