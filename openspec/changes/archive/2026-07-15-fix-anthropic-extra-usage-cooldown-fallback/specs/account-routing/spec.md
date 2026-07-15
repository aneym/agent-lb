## ADDED Requirements

### Requirement: Anthropic extra-usage opt-in overrides only primary exhaustion at pool exhaustion
When `AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE` is true and no subscription-covered account remains eligible in the requested model scope, agent-lb MUST permit an otherwise eligible account with usable extra-usage capacity to serve as the paid last resort even when the credit-billing tripwire has recorded a cooldown for its exhausted primary window. The tripwire MUST use a dedicated quota key distinct from requested-quota cooldowns. Usable extra-usage capacity MUST satisfy `credits_unlimited is true OR (credits_has is true AND credits_balance > 0)`; enabled finite credits with a zero or unknown balance MUST NOT qualify. Paid fallback MUST require every account in the requested model scope to have an active primary-window exhaustion. For Fable requests, only a fresh Fable-scoped exhaustion marker whose reset is still in the future MUST exclude an account from model scope before this pool-wide test; a marker with an elapsed or unknown reset MUST remain in model scope. All remaining accounts MUST remain in model scope, with fresh scoped headroom, weekly heuristic headroom, and fresh capable markers acting as preferences rather than hard admission gates. Active secondary or weekly exhaustion MUST be removed from standard eligibility before those preferences are applied, so an exhausted preferred account cannot hide a usable heuristic soft fallback. Any subscription-covered candidate in that scope, including a heuristic soft fallback, MUST prevent paid fallback. For the exact paid-fallback account IDs and only for the current request, the override MAY bypass a persisted `RATE_LIMITED` status attributable to the active primary-window exhaustion. It MUST NOT clear persisted status or bypass a secondary or weekly `QUOTA_EXCEEDED` status, subscription, model-plan, active Fable hard eligibility, requested-quota cooldown, overload, circuit-breaker, authentication, paused/deactivated, or other non-primary safety gates. When an account is blocked by active primary and secondary windows, its bounded retry time MUST be the later reset; the pool retry time MUST be the earliest bounded effective account reset. An active secondary exhaustion with an unknown reset MUST NOT advertise the earlier primary reset as that account's bounded retry time.

#### Scenario: Credit-backed account serves after every five-hour window is exhausted
- **GIVEN** the extra-usage opt-in is true
- **AND** every subscription-covered candidate is exhausted in its primary window
- **AND** one otherwise eligible exhausted account has usable extra-usage capacity and a dedicated extra-usage tripwire cooldown
- **WHEN** an Anthropic request is routed
- **THEN** agent-lb selects the credit-backed account as the paid last resort

#### Scenario: Subscription-covered candidate remains preferred
- **GIVEN** the extra-usage opt-in is true
- **AND** one candidate has subscription-covered primary-window capacity
- **AND** another exhausted candidate has usable extra-usage capacity
- **WHEN** an Anthropic request is routed
- **THEN** agent-lb selects only from subscription-covered candidates

#### Scenario: Fable scope exhaustion ignores hard-excluded accounts
- **GIVEN** the extra-usage opt-in is true
- **AND** several accounts have primary-window headroom but fresh exhausted Fable-scoped markers
- **AND** every remaining Fable-scoped account is primary-window exhausted
- **AND** one remaining account has usable extra-usage capacity
- **WHEN** a Fable request is routed
- **THEN** agent-lb selects that credit-backed account as the paid last resort
- **AND** any persisted primary-window `RATE_LIMITED` bypass is limited to that account for this request

#### Scenario: Heuristic soft headroom prevents paid spend
- **GIVEN** an exhausted preferred Fable account has usable extra-usage capacity
- **AND** another Fable model-scoped account has primary-window headroom but only heuristic soft eligibility
- **WHEN** a Fable request is routed
- **THEN** agent-lb selects the subscription-covered soft fallback
- **AND** it does not authorize paid fallback

#### Scenario: Secondary-exhausted preference does not hide soft headroom
- **GIVEN** a preferred Fable account has active secondary or weekly exhaustion
- **AND** a heuristic soft Fable account has primary and secondary headroom
- **WHEN** a Fable request is routed
- **THEN** agent-lb excludes the exhausted preferred account before applying Fable preference
- **AND** it selects the subscription-covered soft fallback without authorizing paid traffic

#### Scenario: Disabled opt-in preserves the cooldown
- **GIVEN** the extra-usage opt-in is false
- **AND** every candidate is exhausted in its primary window
- **WHEN** an Anthropic request is routed
- **THEN** agent-lb does not select a credit-backed account
- **AND** it preserves the existing pool-exhausted wait or rate-limit response

#### Scenario: Requested-quota cooldowns remain authoritative
- **GIVEN** the extra-usage opt-in is true
- **AND** an exhausted credit-backed account has an active requested-quota cooldown from an upstream `429`
- **WHEN** an Anthropic request is routed
- **THEN** agent-lb does not bypass that cooldown

#### Scenario: Secondary exhaustion remains authoritative
- **GIVEN** the extra-usage opt-in is true
- **AND** a credit-backed account has active primary-window exhaustion
- **AND** that account also has active secondary or weekly exhaustion
- **WHEN** an Anthropic request is routed
- **THEN** agent-lb does not bypass the secondary or weekly `QUOTA_EXCEEDED` gate
- **AND** a bounded retry reset is no earlier than that account's later active window reset

#### Scenario: Elapsed Fable-scoped reset remains in model scope
- **GIVEN** a fresh Fable-scoped marker is at or above its usage threshold
- **AND** the marker reset has elapsed or is unknown
- **AND** the account has primary-window headroom
- **WHEN** a Fable request is routed with extra usage enabled
- **THEN** the account remains in model scope and prevents paid fallback

#### Scenario: Active Fable hard exclusions retain terminal diagnostics
- **GIVEN** every Fable candidate has a fresh exhausted scoped marker with a future reset
- **WHEN** a Fable request is routed
- **THEN** agent-lb returns a rate-limit response rather than a generic unavailable response
- **AND** it reports the earliest known scoped reset

#### Scenario: Enabled but depleted finite credits remain blocked
- **GIVEN** the extra-usage opt-in is true
- **AND** every candidate is exhausted in its primary window
- **AND** an account reports finite credits enabled with a zero or unknown remaining balance
- **WHEN** an Anthropic request is routed
- **THEN** agent-lb does not select that account as a paid fallback
