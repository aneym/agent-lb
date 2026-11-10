## ADDED Requirements
### Requirement: Usage drains recover without probes
An account that entered the draining or probing tier SHALL return to healthy once its 5-hour and weekly usage are under the drain thresholds and it has no outstanding upstream errors.

#### Scenario: Weekly reset after a drain
- **WHEN** an account drained at 90% weekly use and a reset brings it to 3%
- **THEN** its next selection evaluation marks it healthy and it competes with the other healthy accounts

### Requirement: Anthropic prefers the soonest weekly reset
Anthropic account selection SHALL prefer, among eligible accounts, those whose weekly window ends in the earliest day, then the least weekly use, without bypassing eligibility, budget or sticky-session rules.

#### Scenario: Expiring points are used first
- **WHEN** one usable account's week ends tomorrow and another's ends in five days
- **THEN** a new session is routed to the account whose week ends tomorrow
