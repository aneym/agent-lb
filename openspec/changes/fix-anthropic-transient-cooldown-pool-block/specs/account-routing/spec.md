## ADDED Requirements

### Requirement: Transient requested-quota cooldowns must not terminally block the pool

When no Anthropic account is eligible for the requested quota key and every remaining candidate is blocked only by a requested-quota cooldown whose reset time is within the transient horizon (120 seconds), agent-lb MUST re-admit those candidates for the current request instead of raising the pool-wide cooling-down 429. All such transient candidates MUST be re-admitted so per-request failover can exclude an account that just failed and still try another. An account MUST remain blocked when it has an active primary- or secondary-window exhaustion, an active extra-usage tripwire cooldown, a requested-quota cooldown with an unbounded reset, or a reset beyond the transient horizon. The paid extra-usage last resort MUST retain priority: the transient readmission runs only when the paid fallback produced no candidates. When the bypass triggers, agent-lb MUST log a warning identifying the quota key and readmitted count.

#### Scenario: Small pool under header-less upstream 429s keeps serving

- **GIVEN** two accounts whose requested-quota cooldowns were written from upstream 429 responses carrying no reset headers (60s default resets)
- **WHEN** an Anthropic request is routed while both cooldowns are active
- **THEN** both accounts are re-admitted and the request is attempted upstream instead of failing with the pool-wide cooling-down 429

#### Scenario: Healthy candidate keeps cooled accounts excluded

- **GIVEN** one account with an active near-reset requested-quota cooldown and one healthy account
- **WHEN** an Anthropic request is routed
- **THEN** only the healthy account is eligible

#### Scenario: Genuine window exhaustion still blocks

- **GIVEN** an account with an active primary-window exhaustion and a near-reset requested-quota cooldown
- **WHEN** an Anthropic request is routed
- **THEN** the account remains blocked and the pool-wide error is preserved when no other candidate exists
