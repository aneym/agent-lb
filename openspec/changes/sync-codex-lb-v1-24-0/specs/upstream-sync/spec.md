## ADDED Requirements

### Requirement: Reproducible upstream merge preview

The repository SHALL provide a local command that resolves a fork ref and an
upstream ref to immutable commits and reports their common base, divergent
commit counts, changed upstream paths, conflicted paths, and migration counts.
The command SHALL preserve tracked files, the Git index, and the current branch.
It MAY write temporary merge objects into the Git object database.

#### Scenario: Diverged release requires integration

- **WHEN** the operator previews fork `215823089aca206b5b8536d35415f9cc4a20e943`
  against upstream `84fde5a1ed5e0d5a58ccb3ec4b82938b059bf8c5`
- **THEN** the inventory reports the exact commits and a conflicted merge
- **AND** the preview does not apply conflict resolutions or change runtime code

#### Scenario: Requested ref is unavailable

- **WHEN** a requested ref cannot be resolved locally
- **THEN** the command exits nonzero with a diagnostic
- **AND** it does not fetch or publish anything automatically

### Requirement: Preserve fork behavior during stable-release integration

The completed integration SHALL retain agent-lb's OpenAI and Anthropic provider
support, account-bound routing and egress, quota/cost enforcement, credential
custody, request attribution, operator dashboard, and client entry points.
Integration SHALL preserve Claude billing marker and system-block ordering.
Upstream changes SHALL be reconciled with the fork's module boundaries rather
than marking conflicts resolved through wholesale file selection.

#### Scenario: Upstream modifies an existing fork capability

- **WHEN** an upstream change overlaps a fork provider, account, or routing path
- **THEN** the implementation proves upstream behavior and the fork's existing
  contract with disposable fixtures at the affected product path
- **AND** an unverified path remains an open integration task

### Requirement: Additive migration compatibility

The completed integration SHALL preserve previously shipped fork migrations,
converge to one Alembic head, and prove schema parity after upgrades from both
empty databases and the current fork revision on SQLite and PostgreSQL.

#### Scenario: Existing fork installation is upgraded

- **WHEN** a disposable database at the fork revision is upgraded
- **THEN** existing provider, account, key, and request attribution data remain valid
- **AND** the upgraded database passes the schema check

### Requirement: Honest release readiness

Release readiness SHALL be declared only after the integration tasks and exact-SHA
validation have passed. Branch version metadata MAY identify its imported release
while the integration remains a draft. A conflict inventory, documentation validation, blocked
journey, or draft PR SHALL NOT be represented as a verified runtime upgrade.

#### Scenario: Required validation tooling is unavailable

- **WHEN** a required check cannot run on the current machine
- **THEN** its result is recorded as unavailable with the concrete missing tool
- **AND** the integration remains unverified until that check passes
