# claude-harness-codex — delta

## ADDED Requirements

### Requirement: Fable driver launch profile

The project SHALL provide a `fable` launch profile that runs the canonical Claude Code harness through the launcher with the driver model and the Opus and Fable default model slots resolved to the Fable driver model. The profile MUST leave the Sonnet, Haiku, and subagent model slots unset so the canonical seats continue to resolve through the load balancer, MUST honor an operator-supplied driver-model override environment variable, and MUST let an explicit caller model argument take precedence over the profile's model.

#### Scenario: Fable driver session

- **WHEN** an operator starts a session through the `fable` profile without a model argument
- **THEN** the launched Claude Code driver model is `claude-fable-5`
- **AND** the Opus and Fable default model slots resolve to `claude-fable-5`

#### Scenario: Seat slots survive the driver swap

- **WHEN** the `fable` profile launches a session
- **THEN** the Sonnet, Haiku, and subagent model slots are not written by the profile
- **AND** the Explore, implementer, and verifier seats retain their configured GPT/Sol routes

#### Scenario: Explicit model argument wins

- **WHEN** the `fable` profile is invoked with an explicit model argument
- **THEN** the executed Claude Code command names the caller-supplied model

#### Scenario: Launcher behavior is shared

- **WHEN** the `fable` profile launches a session
- **THEN** it executes the same launcher as the canonical `cc` profile
- **AND** load balancer probing, limit waiting, and session identity behave as they do for `cc`

### Requirement: Fable-primary plan reviewer seat

The canonical Claude Code plan-reviewer child SHALL select the `claude-planner` route alias with high effort and SHALL be read-only, without file-writing tools. It reviews plans, specifications, and design documents before implementation dispatch and MUST NOT modify the plan or the codebase.

#### Scenario: Plan reviewer dispatch

- **WHEN** Claude Code dispatches the canonical `plan-reviewer` agent without a per-invocation model override
- **AND** at least one otherwise-routable account is not hard-excluded by an authoritative Fable-scoped marker
- **THEN** the child request resolves upstream to `claude-fable-5`

#### Scenario: Plan reviewer after full scoped exhaustion

- **WHEN** every otherwise-routable account is hard-excluded by a fresh, future-reset Fable-scoped marker at or above the configured threshold
- **AND** Opus 5 has an eligible route
- **THEN** the child request resolves upstream to `claude-opus-5`

#### Scenario: Plan reviewer cannot write

- **WHEN** the canonical plan-reviewer definition is installed
- **THEN** its tool list excludes file-writing tools
- **AND** the definition disallows Write and Edit
