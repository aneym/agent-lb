## ADDED Requirements

### Requirement: Anthropic sticky sessions rebind on headroom pressure without burn-first targets

Anthropic sticky selection MUST, when `ANTHROPIC_STICKY_HEADROOM_REALLOCATION_ENABLED`
is true (default), proactively rebind a budget-pressured pinned account (primary
used percent above the sticky reallocation threshold) to the best budget-safe
eligible account even when no `burn_first` candidate exists. The rebind MUST persist
the new mapping as the session's durable pin. The existing anti-thrash guard MUST be
preserved: when the pool's best candidate is the pinned account itself or is also
above the threshold, the pin MUST be kept. Non-Anthropic selection paths MUST be
unaffected.

#### Scenario: Fable session migrates before the 429 wall

- **GIVEN** an Anthropic session pinned to an account whose primary window usage is above the sticky reallocation threshold
- **AND** no account carries a `burn_first` routing policy
- **AND** at least one eligible account is below the threshold
- **WHEN** the session's next request is routed
- **THEN** the session rebinds to a budget-safe account and the new mapping is persisted

#### Scenario: Pool-wide exhaustion keeps the pin (anti-thrash)

- **GIVEN** a pinned account above the threshold
- **AND** every other eligible account is also above the threshold
- **WHEN** the session's next request is routed
- **THEN** the existing pin is kept

#### Scenario: No flap-back after the old window resets

- **GIVEN** a session that rebound from account A to account B under headroom pressure
- **WHEN** account A's primary window resets
- **THEN** subsequent requests remain pinned to account B

#### Scenario: Feature can be disabled

- **GIVEN** `ANTHROPIC_STICKY_HEADROOM_REALLOCATION_ENABLED=false`
- **WHEN** an Anthropic sticky request is routed under budget pressure with no burn-first candidates
- **THEN** selection behaves exactly as before this change

### Requirement: Headless launcher turns share a stable session identity

`claude-lb-launch` MUST derive a stable session id from the working directory for
headless (`-p`/`--print`) invocations when `CLAUDE_LB_SESSION_ID` is unset, so
consecutive headless runs from the same directory reuse one sticky route and its
prompt cache. Interactive invocations MUST keep per-process session ids, and an
explicit `CLAUDE_LB_SESSION_ID` MUST always win.

#### Scenario: Looped headless runs stay on one account

- **GIVEN** repeated `claude -p` invocations from the same working directory
- **WHEN** each invocation claims its session route
- **THEN** every claim uses the same session id and resolves to the same sticky mapping

#### Scenario: Explicit session id wins

- **GIVEN** `CLAUDE_LB_SESSION_ID` is set
- **WHEN** the launcher starts in headless or interactive mode
- **THEN** the configured id is used verbatim
