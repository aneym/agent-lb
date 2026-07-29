# usage-windows

## ADDED Requirements

### Requirement: Codex accounts expose no 5-hour window

OpenAI (codex) accounts SHALL NOT be given a synthesized primary (5-hour)
window. When an openai account has no primary usage data, its account
summary primary fields (`primaryRemainingPercent`, primary reset/window
minutes) SHALL be null rather than defaulting to a full 100% gauge. Weekly
usage reported by upstream in the primary slot SHALL continue to be
normalized into the secondary (weekly) window.

#### Scenario: openai account with no usage data

- **GIVEN** an active openai account with no stored usage rows
- **WHEN** the accounts API builds its summary
- **THEN** `usage.primaryRemainingPercent` is null

#### Scenario: weekly-in-primary-slot rows stay weekly

- **GIVEN** an openai account whose latest primary-slot row carries weekly
  window minutes
- **WHEN** account summaries and pool windows are built
- **THEN** the row contributes to the secondary (weekly) window only

### Requirement: Usage summary omits the primary window for codex-only scopes

`/api/usage/summary` SHALL return `primaryWindow: null` when every account
in the requested scope is an openai account (including an unscoped request
against a codex-only pool). Scopes containing at least one non-openai
account SHALL keep the aggregated primary window. An empty scope keeps the
legacy zeroed snapshot.

#### Scenario: provider=openai scope

- **GIVEN** a pool containing openai accounts with weekly usage
- **WHEN** `/api/usage/summary?provider=openai` is requested
- **THEN** the response has `primaryWindow: null` and an aggregated
  `secondaryWindow`

#### Scenario: mixed pool keeps the 5-hour aggregate

- **GIVEN** a pool with both openai and anthropic accounts
- **WHEN** `/api/usage/summary` is requested without a provider filter
- **THEN** `primaryWindow` is non-null and aggregates only accounts that
  still have a 5-hour window
