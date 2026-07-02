## ADDED Requirements

### Requirement: Non-Fable sticky sessions drain to over-threshold accounts

Non-Fable Anthropic sticky sessions SHALL proactively reallocate off under-threshold accounts:
when Fable routing is enabled and a non-Fable request carries a
request-scoped `burn_first` set (accounts at/over the Fable weekly threshold),
a sticky session pinned to an account outside that set reallocates to a
selectable `burn_first` account instead of waiting for budget
pressure, gated on `ANTHROPIC_FABLE_STICKY_DRAIN_ENABLED` (default true). The
trigger MUST mirror the budget-pressure guards: it applies only to
PROMPT_CACHE/STICKY_THREAD/CODEX_SESSION sticky kinds, only outside the
sequential/reset/single-account drain strategies, and never while the pinned
account is rate-limited.

#### Scenario: Sticky non-Fable session migrates to the drain account

- **GIVEN** a non-Fable sticky session pinned to an account under the Fable
  weekly threshold
- **AND** another account at/over the threshold is selectable
- **WHEN** the session's next request is routed
- **THEN** the selection moves to the over-threshold account
- **AND** the sticky mapping is rewritten to that account

#### Scenario: Session re-pins stably on the drain account

- **GIVEN** a sticky session already pinned to an account in the burn set
- **WHEN** its next non-Fable request is routed
- **THEN** the pin is kept and no reallocation is triggered

#### Scenario: No selectable drain target keeps the pin

- **GIVEN** every over-threshold account is rate-limited or otherwise unselectable
- **WHEN** a pinned non-Fable request is routed
- **THEN** the session stays on its pinned under-threshold account

#### Scenario: Fable-class sticky sessions are unaffected

- **GIVEN** a Fable-class request (empty request-scoped burn set)
- **WHEN** the pinned account is selectable
- **THEN** sticky behavior is unchanged by this feature

#### Scenario: Feature can be disabled

- **GIVEN** `ANTHROPIC_FABLE_STICKY_DRAIN_ENABLED=false`
- **WHEN** any sticky selection runs
- **THEN** reallocation behaves exactly as before this change
