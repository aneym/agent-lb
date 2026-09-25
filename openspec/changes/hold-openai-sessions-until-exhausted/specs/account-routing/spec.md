## ADDED Requirements

### Requirement: Pinned OpenAI sessions hold their account until it is exhausted

When `openai_sticky_hold_until_exhausted` is enabled (default), the router MUST keep a pinned OpenAI session (prompt-cache, sticky-thread or Codex-session pin) on its account while that account is selectable. It MUST NOT move the pin for budget pressure, headroom reallocation or burn-first drain. When the pinned account is rate-limited or quota-exceeded and cannot be selected, the router MUST move the pin to the fallback account and persist the new pin.

#### Scenario: Budget pressure

- **WHEN** a conversation is pinned to an account at 85% with an 80% reallocation threshold and another account is at 5%
- **THEN** the request is routed to the pinned account and the pin is unchanged

#### Scenario: Upstream rejects the pinned account

- **WHEN** the pinned account returns a rate-limit error
- **THEN** the request fails over to another account, the pin moves there, and later requests stay there after the old account recovers
