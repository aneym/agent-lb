# usage-refresh-quota-capacity Specification

## Purpose

Define how usage capacity reads plan types and credit-backed quota, primes primary windows and honors operator reset overrides. Entry capability: [usage-refresh-policy](../usage-refresh-policy/spec.md).

## Requirements

### Requirement: Usage capacity recognizes upstream ChatGPT plan types

The system MUST recognize account plan types returned by upstream ChatGPT auth and usage payloads when calculating absolute usage capacity. `prolite` MUST be treated as a supported account plan with Plus x5 capacity values (`1125.0` primary and `37800.0` secondary), while preserving the stored plan type value for display and request-log context.

#### Scenario: Pro Lite account contributes aggregate remaining credits

- **GIVEN** an active account whose stored `plan_type` is `prolite`
- **AND** its latest primary and secondary usage rows report `used_percent` below 100
- **WHEN** the system builds usage window summaries or per-account remaining credit values
- **THEN** the account contributes `1125.0` primary capacity and `37800.0` secondary capacity
- **AND** the computed remaining credits are non-zero according to the reported usage percent

### Requirement: Pro Lite accounts are eligible for Pro-gated models

The system MUST treat stored `prolite` account plan types as Pro-equivalent when evaluating model registry plan eligibility, while preserving the stored `prolite` value for display and request-log context.

#### Scenario: Pro Lite account can be selected for a Pro-gated model

- **GIVEN** an active account whose stored `plan_type` is `prolite`
- **AND** its latest primary and secondary usage rows are below the configured usage threshold
- **AND** the requested model is allowed for `pro` accounts by the model registry
- **WHEN** proxy account selection evaluates eligible accounts for the requested model
- **THEN** the Pro Lite account remains eligible for selection
- **AND** the selection does not fail with `no_accounts`

### Requirement: Credit-backed secondary quota remains usable

When account status is derived from persisted usage snapshots, an exhausted secondary-window usage percentage MUST NOT by itself mark an account `quota_exceeded` if the governing usage snapshot reports usable credit-backed capacity. Usable credit-backed capacity is present when `credits_unlimited` is true, `credits_has` is true, or `credits_balance` is positive.

This credit-aware interpretation MUST be shared by proxy account selection and account/dashboard summary status mapping so an account selected as usable by the proxy is not simultaneously displayed as `quota_exceeded` in the operator summary. Exhausted primary-window usage MUST still take precedence as `rate_limited`, and paused or deactivated accounts MUST NOT be reactivated solely because a usage snapshot reports usable credits.

#### Scenario: Secondary quota exhausted with credits remains active

- **GIVEN** an account is persisted as `quota_exceeded`
- **AND** its governing secondary-window usage reports `used_percent >= 100`
- **AND** the same usage snapshot reports usable credit-backed capacity
- **WHEN** proxy selection or account-summary mapping derives the effective status
- **THEN** the effective status is `active`

#### Scenario: Exhausted primary window keeps rate-limit precedence

- **GIVEN** an account has usable credit-backed capacity in its usage snapshot
- **AND** its primary-window usage reports `used_percent >= 100`
- **WHEN** proxy selection or account-summary mapping derives the effective status
- **THEN** the effective status is `rate_limited`

#### Scenario: Operator-disabled states are preserved

- **GIVEN** an account is `paused` or `deactivated`
- **AND** its usage snapshot reports usable credit-backed capacity
- **WHEN** proxy selection or account-summary mapping derives the effective status
- **THEN** the account remains `paused` or `deactivated`

### Requirement: Continuous Anthropic primary-window priming

The scheduler MUST prime each active, opted-in Anthropic account whose five-hour primary window is closed, regardless of the legacy warmup working-hours band or the global OpenAI warmup setting. A window is closed when fresh usage (at most ten minutes old) shows `used_percent == 0` and either no `reset_at` or a `reset_at` the sample was recorded after. The primer is one one-token Haiku Messages request. Attempts are keyed by the start of the fixed window-length bucket containing the decision time, in the durable attempts table, so at most one primer per account and bucket is sent. The weekly window rolls on a fixed per-account schedule and is never primed.

#### Scenario: An idle account's five-hour window has closed

- **GIVEN** an active, opted-in Anthropic account with a nonexhausted weekly window
- **WHEN** fresh usage shows the primary window closed, even hours after the reset and without any Haiku quota sample
- **THEN** the scheduler sends one minimal primer
- **AND** a later usage refresh that shows a primary reset four to six hours after the send confirms it
- **AND** later scheduler passes and process restarts MUST NOT send a duplicate while the window is reported closed

#### Scenario: A primer did not visibly open a window

- **WHEN** usage recorded after a sent primer still shows the primary window closed
- **THEN** the scheduler MUST NOT send another primer until nearly a full window length after the previous send, even across a bucket boundary

#### Scenario: A send explicitly fails

- **WHEN** an Anthropic primer returns an upstream error or transport failure
- **THEN** the failed attempt is retained and retried only after backoff, with one in-flight claimant and at most five retries per bucket

#### Scenario: Priming would risk overage

- **WHEN** the account is disabled, quota-exceeded, weekly-capped, unsubscribed, its Haiku quota sample is exhausted, or fresh primary or weekly usage is missing
- **THEN** the scheduler MUST NOT send a primer or enter the extra-usage credit-billing path

#### Scenario: Operator observes primer state

- **WHEN** a primer is attempted and later confirmed
- **THEN** the service logs the attempt and result without credentials
- **AND** account status exposes the last confirmed prime time

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
