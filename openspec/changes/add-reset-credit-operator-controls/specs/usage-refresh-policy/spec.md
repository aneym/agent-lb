## MODIFIED Requirements

### Requirement: Banked reset credits are auto-redeemed only on full pool exhaustion

The service MUST run a leader-elected background scheduler that automatically
redeems banked rate-limit reset credits only when no subscription-usable
OpenAI account in a serving status remains `active`, and MUST bound automatic
spend to a daily allowance of `reset_credit_auto_redeem_max_per_day`
redemptions per rolling 24 hours, separated by at least
`reset_credit_auto_redeem_cooldown_seconds`.

#### Scenario: Full exhaustion triggers exactly one redemption

- **WHEN** every subscription-usable OpenAI account with status in `active`, `rate_limited`, `quota_exceeded` has status `rate_limited` or `quota_exceeded`
- **AND** at least one such account has an available banked reset credit
- **AND** the rolling-24h allowance is not yet spent and the spacing cooldown has elapsed
- **THEN** the scheduler redeems exactly one credit — preferring `quota_exceeded` accounts, then the earliest-expiring available credit
- **AND** the redemption path refreshes the account's usage snapshot and invalidates the selection cache
- **AND** the scheduler sends a wake probe to the account so the upstream limiter re-evaluates
- **AND** the redemption is audit-logged as `account_reset_credit_redeemed` with `trigger: auto`

#### Scenario: A same-day re-limit still gets a reset

- **WHEN** one automatic redemption was already applied within the last 24 hours
- **AND** the pool becomes fully exhausted again and the spacing cooldown has elapsed
- **THEN** the scheduler redeems one further credit, up to `reset_credit_auto_redeem_max_per_day` applied redemptions in the rolling window

#### Scenario: Daily allowance bounds unattended spend

- **WHEN** `reset_credit_auto_redeem_max_per_day` automatic redemptions have been applied within the last 24 hours
- **THEN** the scheduler redeems nothing further on the exhaustion path, however exhausted the pool is and however many credits are banked
- **AND** the allowance is counted from the durable attempt ledger, so a restart does not restore it

#### Scenario: Only applied automatic redemptions spend the allowance

- **WHEN** an automatic attempt settled without being applied (upstream `nothing_to_reset`)
- **OR** a redemption was triggered by the expiry sweep (`trigger: expiring`) or by an operator (`trigger: manual`)
- **THEN** it does not count against the automatic daily allowance

#### Scenario: Kill switch

- **WHEN** `reset_credit_auto_redeem_enabled` is `false` **OR** `reset_credit_auto_redeem_max_per_day` is `0`
- **THEN** the scheduler performs no exhaustion-triggered redemption

## ADDED Requirements

### Requirement: Operators can spend a banked reset credit from the dashboard and the menubar

Both operator surfaces MUST expose a per-account control that redeems one
banked rate-limit reset credit through
`POST /api/accounts/{account_id}/rate-limit-reset-credits/consume`, and MUST
report the upstream `code` rather than the HTTP status as the verdict.
Operator-initiated redemption MUST NOT be blocked by the automatic daily
allowance or its spacing cooldown.

#### Scenario: Dashboard reset control

- **WHEN** an OpenAI account that is neither `paused` nor awaiting operator recovery is selected
- **THEN** the account actions show a "Reset limits (N)" control carrying the banked-credit count
- **AND** the control is disabled when `N` is zero
- **AND** activating it asks for confirmation stating the credit cannot be refunded before any request is sent
- **AND** on completion the account list, trends and dashboard overview queries are invalidated

#### Scenario: Menubar reset control

- **WHEN** a Codex account row is limit-blocked (`rate_limited` or `quota_exceeded`), routable, and holds at least one banked credit
- **THEN** its `⟲ N` chip is actionable and a "Reset Rate Limits (N banked)" context-menu item is offered
- **AND** the row shows in-flight and failed states inline without changing row height
- **AND** paused, disconnected, unsubscribed, non-Codex, `active`, and zero-credit rows offer no reset action

#### Scenario: A no-op redemption is not reported as recovered capacity

- **WHEN** the endpoint answers 200 with code `nothing_to_reset` or `no_credit`
- **THEN** the surface reports that nothing was reset (the credit stays banked / the bank is empty), not success
