## ADDED Requirements

### Requirement: Active ladders govern seat selection

The router SHALL walk the active ladder when it defines the requested class, retaining baseline class-chain behavior otherwise. Closed gates, unmet conditions, exhausted pools, unavailable models, failed seats and missing cross-maker reviewers SHALL exclude rungs. Low pace and orchestrator reserve SHALL defer rungs, but permit the first deferred rung when no other rung is open.

#### Scenario: Low Codex passes only an open gate

- **WHEN** Codex is running low and the Grok implementation gate is closed
- **THEN** Sol remains selected with the low-pace fallback reason and Sonnet high as reviewer
- **WHEN** the same gate opens
- **THEN** Grok medium at standard speed is selected with Sol as the intended rung

### Requirement: Ladder routing is inspectable

The router SHALL expose rung, intended rung, maker, effort, pace, skipped reasons and reviewer in ladder picks. Menu SHALL expose every rung and its status. Seat SHALL use the first matching class rung. Seats SHALL expose ladders with verification references expanded.

#### Scenario: Baseline is reversible

- **WHEN** the active ladder is baseline
- **THEN** existing class-chain picks and their output format are preserved
