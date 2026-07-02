## ADDED Requirements

### Requirement: Usage summary endpoint supports optional provider scoping

`GET /api/usage/summary` MUST accept an optional `provider` query parameter
(lowercase provider name, e.g. `anthropic`/`openai`). When present, the
returned summary (windows, cost, metrics) MUST be computed only over that
provider's subscription-usable accounts, and unattributed request logs
(`account_id IS NULL`) MUST be excluded from a scoped response. An unknown
provider value MUST return a valid, structurally-complete empty summary
(zeroed windows/cost/metrics), never an error response. Omitting the
parameter MUST leave the existing pool-global response unchanged.

#### Scenario: Scoped request filters to the provider's accounts

- **GIVEN** a pool with both Anthropic and OpenAI subscription-usable
  accounts, each with attributed request logs
- **WHEN** a client calls `GET /api/usage/summary?provider=anthropic`
- **THEN** the returned windows, `cost.totalUsd7d`, and metrics reflect only
  Anthropic accounts' attributed usage

#### Scenario: Unattributed logs are excluded from a scoped summary

- **GIVEN** request logs exist with `account_id IS NULL` (e.g. pre-
  attribution or key-only traffic) alongside logs attributed to specific
  accounts
- **WHEN** a client calls `GET /api/usage/summary?provider=<name>`
- **THEN** the unattributed logs are excluded from the scoped totals
- **AND** an unscoped call (`GET /api/usage/summary`, no `provider` param)
  continues to include them, unchanged from prior behavior

#### Scenario: Unknown provider returns an empty summary, not an error

- **WHEN** a client calls `GET /api/usage/summary?provider=doesnotexist`
- **THEN** the response is `200 OK` with a structurally valid summary whose
  windows/cost/metrics are all zeroed or empty
- **AND** no `4xx`/`5xx` status is returned solely because the provider name
  is unrecognized

#### Scenario: Omitting the parameter preserves existing behavior

- **GIVEN** any pool state
- **WHEN** a client calls `GET /api/usage/summary` with no `provider` param
- **THEN** the response is byte-for-byte equivalent (same fields, same
  values) to the endpoint's behavior before this change
