## ADDED Requirements

### Requirement: Anthropic extra-usage tripwire follows current subscription exhaustion
The router SHALL honor a persisted Anthropic extra-usage tripwire only while the account has active primary subscription-window exhaustion, defined as latest primary usage of at least 100 percent with a known reset strictly in the future. The router SHALL apply this rule consistently to normal eligibility and transient request-quota cooldown recovery. Independent request-quota, secondary-window, model, and explicit paid-fallback rules SHALL remain in effect.

#### Scenario: Recovered subscription headroom
- **GIVEN** an account has an active extra-usage tripwire and latest primary usage below 100 percent
- **WHEN** ordinary routing eligibility is evaluated
- **THEN** the historical tripwire SHALL NOT exclude the account

#### Scenario: Recovered account has a transient request cooldown
- **GIVEN** an account has primary headroom, an active historical extra-usage tripwire, and a request-quota cooldown within the existing transient recovery horizon
- **WHEN** the account otherwise qualifies for transient cooldown recovery
- **THEN** the tripwire SHALL NOT prevent that recovery

#### Scenario: Primary subscription remains exhausted
- **GIVEN** latest primary usage is at least 100 percent with a future reset and an active extra-usage tripwire
- **WHEN** ordinary subscription routing eligibility is evaluated
- **THEN** the account SHALL remain excluded under the existing safety gates

#### Scenario: Primary exhaustion has no active bounded reset
- **GIVEN** an account has an active extra-usage tripwire and primary usage whose reset is missing or no longer in the future
- **WHEN** routing eligibility is evaluated
- **THEN** the historical tripwire SHALL NOT independently exclude the account
