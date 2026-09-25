## ADDED Requirements

### Requirement: Pinned Claude sessions hold their account until it is exhausted

When `anthropic_sticky_hold_until_exhausted` is enabled (default), the router MUST keep a pinned Claude session on its account while that account is eligible, regardless of how much of its 5-hour or weekly window is used. It MUST NOT move the pin for budget pressure, headroom reallocation, or burn-first drain. When the pinned account becomes ineligible (a live upstream quota rejection or another hard exclusion), the router MUST move the pin once to the least-used eligible account and persist the new pin.

#### Scenario: Nearly exhausted account

- **WHEN** a session is pinned to an account at 97% of its 5-hour window and another account is at 5%
- **THEN** the request is routed to the pinned account and the pin is unchanged

#### Scenario: Upstream rejects the pinned account

- **WHEN** the pinned account carries a live quota cooldown
- **THEN** the request is routed to the least-used eligible account and the pin moves there

### Requirement: Burn-first preference is opt-in

The router MUST compute a burn-first account set for non-Fable Claude traffic only when `anthropic_fable_burn_first_enabled` is true. The default is false.
