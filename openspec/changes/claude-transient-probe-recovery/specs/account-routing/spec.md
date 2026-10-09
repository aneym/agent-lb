## ADDED Requirements

### Requirement: Successful probes reconcile transient routing backoff
A successful pinned account probe MUST clear transient error backoff in the shared Anthropic-compatible selector, without clearing quota cooldowns or enabling paid fallback. An unsuccessful probe MUST NOT clear transient backoff.

#### Scenario: Mirror failure burst followed by healthy probe
- **GIVEN** an account excluded by transient errors
- **WHEN** a pinned probe succeeds
- **THEN** normal routing can select it without restarting the service

#### Scenario: Probe still fails
- **GIVEN** an account excluded by transient errors
- **WHEN** a pinned probe fails
- **THEN** its transient backoff remains

### Requirement: Transient exclusion reports its recovery deadline
A transient backoff exclusion MUST expose its own retry deadline and identify transient backoff rather than attributing it to another account's quota reset.

#### Scenario: Sole quota-eligible account backs off
- **GIVEN** the sole quota-eligible account is in transient backoff
- **WHEN** routing reports no available account
- **THEN** the reported retry deadline is the transient backoff deadline
