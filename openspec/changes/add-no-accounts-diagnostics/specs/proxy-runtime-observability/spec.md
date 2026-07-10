## ADDED Requirements

### Requirement: No-accounts selection failures are diagnosable end to end

When account selection fails with no selectable account, the proxy MUST retain the per-account exclusion detail computed during selection (account id, status, reset time, cooldown expiry, deactivation reason, plan type) and the earliest future recovery time across resets and cooldowns, and MUST expose them on the terminal selection result instead of discarding them.

#### Scenario: all accounts rate limited

- **GIVEN** every account in the pool is excluded (rate limited, cooling down, paused, deactivated, or requiring re-authentication)
- **WHEN** account selection runs for an incoming request
- **THEN** the selection failure carries one exclusion entry per account with its status and any known reset or cooldown time
- **AND** the selection failure carries the earliest future recovery time across all excluded accounts, or none when no recovery time is known

### Requirement: No-accounts error envelopes carry recovery metadata

No-accounts OpenAI-compatible error responses (HTTP bridge, proxy service, and streaming SSE error events) MUST include `resets_at` and `resets_in_seconds` when an earliest recovery time is known, and MUST include an `error.diagnostics` object with `degraded`, per-account `accounts` entries (`id`, `status`, `reset_at`, `reason`), `earliest_recovery_at` when known, and `requested_model` when available. These fields MUST be additive to the standard OpenAI error envelope shape.

#### Scenario: no-accounts 503 with known recovery

- **GIVEN** account selection fails and the earliest excluded account resets at a known future time
- **WHEN** the proxy returns the no-accounts error response
- **THEN** the error detail includes `resets_at` and a non-negative `resets_in_seconds`
- **AND** the error detail includes `diagnostics.accounts` describing each excluded account
- **AND** the error detail includes `diagnostics.earliest_recovery_at` in UTC ISO-8601 form

#### Scenario: selection failure without account detail

- **GIVEN** a selection failure that carries no per-account exclusion detail
- **WHEN** the proxy builds the error response
- **THEN** the envelope omits `diagnostics` rather than emitting an empty object

### Requirement: Retry-After derives from selection recovery metadata

Logged 429 and 503 error responses whose error detail includes a non-negative `resets_in_seconds` MUST include a `Retry-After` header with the ceiled seconds value, unless a `Retry-After` header is already present.

#### Scenario: 503 gains Retry-After

- **GIVEN** a no-accounts 503 whose error detail carries `resets_in_seconds`
- **WHEN** the response is returned to the client
- **THEN** the response includes a `Retry-After` header matching the ceiled `resets_in_seconds`

### Requirement: Fleet availability endpoint

The service MUST expose `GET /api/availability` behind the dashboard session guard, returning per-provider account availability: total accounts, available count, unavailable accounts with `id`, `status`, and known `reset_at`, the provider's earliest recovery time when known, and the current degradation status (`level`, `reason`).

#### Scenario: mixed-provider pool with one unavailable seat

- **GIVEN** authenticated accounts across providers where one account is not `active`
- **WHEN** a dashboard-authenticated client requests `GET /api/availability`
- **THEN** the response groups accounts per provider with `total` and `available` counts
- **AND** the unavailable account appears with its status and known reset time
- **AND** the response includes the current degradation level and reason
