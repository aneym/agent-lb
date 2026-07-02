## ADDED Requirements

### Requirement: Over-threshold accounts keep being tried for Fable

The balancer SHALL empirically verify — rather than assume — that accounts at/over the Fable weekly threshold cannot serve Fable-class requests:
the account pulse sends each such routable Anthropic account a minimal real
Fable-class probe each cycle (gated on
`ANTHROPIC_FABLE_OVER_THRESHOLD_PROBE_ENABLED`, default true), records the
outcome as a Fable-access marker, and Fable routing includes over-threshold
accounts whose latest fresh marker is capable. A probe outcome MUST NOT
modify general account status, subscription state, or non-Fable routing.

#### Scenario: Upstream serves Fable past the threshold

- **GIVEN** an account at/over the weekly threshold whose latest Fable probe
  succeeded within the marker TTL
- **WHEN** a Fable-class request is routed
- **THEN** the account is a candidate alongside under-threshold accounts

#### Scenario: Upstream refuses Fable

- **GIVEN** an account whose latest Fable probe was refused (4xx
  model/permission error)
- **WHEN** Fable-class requests are routed before the account's weekly window
  resets
- **THEN** the account is excluded from the Fable pool
- **AND** the pulse probes it again after the weekly window resets

#### Scenario: No fresh evidence keeps the safe default

- **GIVEN** an over-threshold account with no marker, a stale marker, or an
  inconclusive probe (429/5xx)
- **WHEN** a Fable-class request is routed
- **THEN** the account stays excluded until a fresh successful probe

#### Scenario: Probe never changes account health

- **WHEN** a Fable probe is refused upstream
- **THEN** the account's status, subscription ledger, and non-Fable routing
  are unchanged

#### Scenario: Probing can be disabled

- **GIVEN** `ANTHROPIC_FABLE_OVER_THRESHOLD_PROBE_ENABLED=false`
- **WHEN** the pulse runs
- **THEN** no Fable probes are sent and eligibility behaves as before this
  change
