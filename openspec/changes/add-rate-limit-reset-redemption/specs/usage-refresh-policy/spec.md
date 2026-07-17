## ADDED Requirements

### Requirement: Operators can list an account's banked rate-limit reset credits

The dashboard MUST expose an admin-only endpoint that fetches an OpenAI-provider
account's saved rate-limit reset credits from the upstream
`wham/rate-limit-reset-credits` endpoint using that account's own credentials.

#### Scenario: Listing reset credits

- **WHEN** an operator GETs `/api/accounts/{account_id}/rate-limit-reset-credits`
- **AND** the account exists, has provider `openai`, and is not `paused`, `deactivated`, or `reauth_required`
- **THEN** the service refreshes credentials if needed and calls upstream `GET {upstream_base_url}/backend-api/wham/rate-limit-reset-credits` with the account's bearer token and `chatgpt-account-id`
- **AND** the response carries `available_count` and each credit's `id`, `reset_type`, `status`, `granted_at`, `expires_at`, `title`, `description`

#### Scenario: Listing rejects non-OpenAI or blocked accounts

- **WHEN** an operator GETs `/api/accounts/{account_id}/rate-limit-reset-credits`
- **AND** the account provider is not `openai`, or its status is `paused`, `deactivated`, or `reauth_required`
- **THEN** the endpoint responds `409` with code `account_reset_credits_unavailable`
- **AND** no upstream request is sent

#### Scenario: Unknown account returns 404

- **WHEN** an operator GETs `/api/accounts/{account_id}/rate-limit-reset-credits`
- **AND** no account with that id exists
- **THEN** the endpoint responds `404` with code `account_not_found`

### Requirement: Operators can redeem a banked rate-limit reset credit

The dashboard MUST expose an admin-only endpoint that consumes one saved reset
credit for an OpenAI-provider account via upstream
`wham/rate-limit-reset-credits/consume`, then immediately refreshes the
account's usage snapshot so the balancer observes the reset windows.

#### Scenario: Redeeming a reset credit resets exhausted windows

- **WHEN** an operator POSTs `/api/accounts/{account_id}/rate-limit-reset-credits/consume` (optionally with `credit_id`)
- **AND** the account exists, has provider `openai`, and is not `paused`, `deactivated`, or `reauth_required`
- **THEN** the service sends upstream `POST {upstream_base_url}/backend-api/wham/rate-limit-reset-credits/consume` with a freshly generated UUID `redeem_request_id` and the optional `credit_id`
- **AND** on upstream codes `reset` and `already_redeemed` the service triggers an immediate usage refresh for the account and invalidates the account selection cache
- **AND** the response carries the upstream `code`, `windows_reset`, and before/after `primary_used_percent` / `secondary_used_percent`
- **AND** the redemption attempt is recorded in the audit log with the upstream code

#### Scenario: Redemption without an available credit is surfaced, not retried

- **WHEN** the upstream consume call returns code `no_credit` or `nothing_to_reset`
- **THEN** the endpoint responds `200` with that code and `windows_reset` unchanged
- **AND** the service does not retry the consume call

#### Scenario: Redemption rejects non-OpenAI or blocked accounts

- **WHEN** an operator POSTs `/api/accounts/{account_id}/rate-limit-reset-credits/consume`
- **AND** the account provider is not `openai`, or its status is `paused`, `deactivated`, or `reauth_required`
- **THEN** the endpoint responds `409` with code `account_reset_credits_unavailable`
- **AND** no upstream request is sent
