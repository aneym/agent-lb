## ADDED Requirements

### Requirement: Operators can explicitly override the local Claude reset daily limit

The admin `POST /api/accounts/{account_id}/rate-limit-reset-credits/consume` endpoint MUST accept an optional `overrideDailyLimit` boolean defaulting to false. For a manual Anthropic redemption with `overrideDailyLimit: true`, the service MUST bypass only its global rolling-24h applied-Claude-reset check. It MUST still require an authenticated, subscription-usable account, a currently usable provider grant, and the globally serialized active-attempt slot. It MUST preserve unknown-outcome no-retry behavior. The durable attempt MUST record trigger `manual_override`, and the API audit details MUST record the override flag. Without the flag, a prior applied Claude reset in the last 24 hours MUST continue to produce `409` with code `account_reset_credits_unavailable`.

#### Scenario: Manual override follows an earlier applied reset

- **GIVEN** a Claude reset was applied within the last 24 hours
- **AND** another Claude account has a currently usable grant
- **WHEN** the operator POSTs the consume endpoint with `overrideDailyLimit: true`
- **THEN** one new reset may be redeemed through the normal attempt and recovery flow
- **AND** the attempt trigger is `manual_override` and the API audit details include `override_daily_limit: true`

#### Scenario: Ineligible grant cannot be spent through override

- **WHEN** a manual Claude consume request sets `overrideDailyLimit: true` but the provider reports no currently usable grant
- **THEN** the endpoint returns the normal `not_eligible` result without calling provider redemption

#### Scenario: Override is not available to automatic or OpenAI redemption

- **WHEN** an automatic or expiry-triggered service call requests a daily-limit override
- **THEN** the service rejects it before consuming a credit
- **AND** an automatic call without the override remains subject to the Claude rolling-24h cap
- **WHEN** the API receives `overrideDailyLimit: true` for an OpenAI account
- **THEN** it returns `409` with code `account_reset_credits_unavailable` without consuming a credit
