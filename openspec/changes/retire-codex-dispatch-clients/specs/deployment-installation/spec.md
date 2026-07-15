# deployment-installation delta

## REMOVED Requirements

### Requirement: Claude Code client wiring includes the CCDEX worker

**Reason**: The `ccdex` and `ccdex-worker-mcp` clients, the CCDEX guard hook,
and the codex-host instruction adapter are retired (2026-07-15). Replaced by
the narrowed "Claude Code client wiring" requirement below.

## ADDED Requirements

### Requirement: Claude Code client wiring

The project SHALL provide a deterministic installer for the canonical
coding-agent policy and the `cc` client executable. Installation MUST converge
the routing-owned portion of the global Claude instructions, MUST remove the
retired managed routing block from the global Codex instructions when present,
MUST set direct Claude Code to the canonical Fable model, and MUST remove
retired ccdex artifacts (client symlinks, the guard-hook symlink, and the
user-scoped `ccdex-worker` MCP registration) when it finds them. Installation
MUST preserve unrelated Markdown sections, JSON keys, permission rules, hooks,
environment values, and machine-specific configuration. Re-running installation
MUST converge without duplicating managed blocks or registrations, and removal
MUST be explicit rather than coupled to service uninstall.

#### Scenario: First client install

- **WHEN** an operator installs Claude Code client wiring
- **THEN** the `cc` entry point resolves to the current checkout
- **AND** the host-neutral coding-agent policy path resolves to the versioned repository policy
- **AND** the global Claude instruction file contains one canonical routing adapter
- **AND** direct Claude Code defaults to the canonical Fable model

#### Scenario: Machine carrying retired ccdex artifacts

- **GIVEN** a machine with `ccdex`/`ccdex-worker-mcp` symlinks, a guard-hook
  symlink, a managed routing block in the global Codex instructions, and owned
  guard-hook registrations in Claude settings
- **WHEN** the client installer runs
- **THEN** the retired symlinks, managed Codex block, hook registrations, and
  `ccdex-worker` MCP registration are removed
- **AND** unrelated Markdown sections, JSON values, and hooks remain present

#### Scenario: Repeated client install

- **WHEN** the client installer runs again for the same checkout
- **THEN** the policy link, `cc` link, managed routing block, and model field
  converge on the same targets
- **AND** it does not create duplicate managed blocks or registrations

#### Scenario: Preview install

- **WHEN** the installer runs with `--print`
- **THEN** it reports the links, managed-configuration convergence, and
  retired-artifact cleanup it would perform without mutating anything
