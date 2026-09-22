## MODIFIED Requirements

### Requirement: Explicit Claude Code launch profiles

The launcher and installed `cc` alias SHALL default normal Claude Code sessions to canonical Opus 5 with high effort when the caller supplies no model or effort, and SHALL force CCDEX sessions to the canonical compatibility model with high effort regardless of caller model or effort arguments.

#### Scenario: Normal Claude Code default

- **WHEN** the normal launcher is invoked without a model or effort override
- **THEN** the executed Claude Code command names `claude-opus-5` and high effort

#### Scenario: CCDEX ignores conflicting controls

- **WHEN** CCDEX is invoked with caller-supplied model or effort controls
- **THEN** the executed Claude Code command names only the canonical compatibility model and high effort

### Requirement: Opus frontend designer seat

The canonical Claude Code frontend-designer child SHALL resolve to `claude-opus-5` for design direction and visual critique and MUST NOT inherit or explicitly select Fable 5 or Opus 4.8.

#### Scenario: Frontend designer dispatch

- **WHEN** Claude Code dispatches the canonical `frontend-designer` agent without a per-invocation model override
- **THEN** the child resolves to `claude-opus-5`
- **AND** the request does not consume Fable 5 or Opus 4.8 capacity

#### Scenario: No legacy Claude fallback configuration

- **WHEN** the canonical frontend-designer definition is installed
- **THEN** its model field selects the Claude Code Opus selector that resolves to `claude-opus-5`
- **AND** the definition does not configure Fable 5 or Opus 4.8 as a fallback

## ADDED Requirements

### Requirement: Fable-primary planner seat with scoped Opus 5 fallback

The canonical Claude Code planner child SHALL select the `claude-planner` route alias with high effort. The alias MUST resolve to `claude-fable-5` while any otherwise-routable account remains outside authoritative Fable-scoped exhaustion and MUST resolve to `claude-opus-5` only when every otherwise-routable account has a fresh, future-reset Fable-scoped marker at or above the configured threshold and Opus has an eligible route.

#### Scenario: Planner dispatch with Fable capacity

- **WHEN** Claude Code dispatches the canonical `planner` agent without a per-invocation model override
- **AND** at least one otherwise-routable account is not hard-excluded by an authoritative Fable-scoped marker
- **THEN** the child request resolves upstream to `claude-fable-5`

#### Scenario: Planner dispatch after full scoped exhaustion

- **WHEN** every otherwise-routable account is hard-excluded by a fresh, future-reset Fable-scoped marker at or above the configured threshold
- **AND** Opus 5 has an eligible route
- **THEN** the child request resolves upstream to `claude-opus-5`

#### Scenario: Non-scoped failures do not trigger planner fallback

- **WHEN** Fable routing encounters partial exhaustion, a soft weekly threshold, a stale or missing scoped marker, a generic rate limit, auth failure, network or 529 failure, or total Anthropic quota exhaustion
- **THEN** the planner alias does not switch to Opus 5 because of that condition

#### Scenario: API-key policy uses the effective planner model

- **WHEN** a request authenticated by a restricted API key selects `claude-planner`
- **THEN** model admission and initial usage reservation use the resolved `claude-fable-5` or `claude-opus-5` model
- **AND** the effective model used for upstream forwarding, request logging, pricing, and final settlement matches the reservation model
- **AND** allowing the alias alone does not bypass an API key policy that disallows the effective model

### Requirement: Claude Code route migration isolation

The Opus 5 migration SHALL preserve the canonical GPT/Sol compatibility and worker-seat routes, including their model, effort, and service-tier behavior. Fable-specific account capability probes, cooldown keys, quota state, usage attribution, pricing, and historical request data MUST retain their Fable meaning and MUST NOT be treated as current Claude Code route assignments.

#### Scenario: GPT and Sol seats remain fixed

- **WHEN** the canonical Claude Code routing policy is migrated to Opus 5
- **THEN** the CCDEX compatibility route and the Explore, implementer, and verifier seats retain their existing GPT/Sol models, effort, and service-tier behavior

#### Scenario: Fable telemetry remains Fable-specific

- **WHEN** the Opus 5 routing migration is applied
- **THEN** Fable account probes, cooldown keys, quota state, pricing, usage attribution, and historical model values remain unchanged
