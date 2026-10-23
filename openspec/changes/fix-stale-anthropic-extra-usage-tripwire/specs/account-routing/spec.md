## ADDED Requirements

### Requirement: Anthropic extra-usage tripwire follows current subscription exhaustion
The router SHALL disregard a persisted extra-usage tripwire only when a strictly newer primary snapshot no longer shows active exhaustion (usage at least 100 percent with a future reset). Missing, older, or equally timed primary snapshots SHALL retain the tripwire. Normal eligibility and transient request-cooldown recovery SHALL use the same rule. Independent request, secondary, model, and paid-fallback gates SHALL remain effective.

#### Scenario: Recovered subscription headroom
- **GIVEN** an account has an active extra-usage tripwire and a newer primary usage snapshot below 100 percent
- **WHEN** ordinary routing eligibility is evaluated
- **THEN** the historical tripwire SHALL NOT exclude the account

#### Scenario: Recovered account has a transient request cooldown
- **GIVEN** an account has primary headroom recorded after an active historical extra-usage tripwire, and a request-quota cooldown within the existing transient recovery horizon
- **WHEN** the account otherwise qualifies for transient cooldown recovery
- **THEN** the tripwire SHALL NOT prevent that recovery

#### Scenario: Primary subscription remains exhausted
- **GIVEN** latest primary usage is at least 100 percent with a future reset and an active extra-usage tripwire
- **WHEN** ordinary subscription routing eligibility is evaluated
- **THEN** the account SHALL remain excluded under the existing safety gates

#### Scenario: Primary exhaustion has no active bounded reset
- **GIVEN** an account has an active extra-usage tripwire and newer primary usage whose reset is missing or no longer in the future
- **WHEN** routing eligibility is evaluated
- **THEN** the historical tripwire SHALL NOT independently exclude the account


#### Scenario: Fresh credit-billing response precedes usage refresh
- **GIVEN** an account has an active extra-usage tripwire from a credit-billing response and primary usage is missing or was recorded at or before the tripwire
- **WHEN** the next request is routed
- **THEN** the tripwire SHALL exclude that account and allow an eligible fallback account to serve the request
