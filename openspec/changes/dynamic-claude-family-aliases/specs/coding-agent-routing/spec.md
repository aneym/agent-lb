## ADDED Requirements

### Requirement: Claude family aliases resolve to the newest listed model

`route` MUST resolve a Claude family alias to the newest non-retired `claude-<family>-<major>[-<minor>]` id on the upstream Anthropic model list the LB serves, falling back to its day-old cache, then the alias's `pinned` id, then the harness alias.

#### Scenario: A new Sonnet appears upstream

- **WHEN** the upstream list gains `claude-sonnet-6` and no alias names it
- **THEN** `route resolve sonnet-latest` prints `claude-sonnet-6`
- **AND** the next policy install writes `ANTHROPIC_DEFAULT_SONNET_MODEL=claude-sonnet-6`

#### Scenario: Dated snapshots and retired ids are skipped

- **WHEN** the list holds `claude-sonnet-4-20250514` and the retired `claude-sonnet-5`
- **THEN** neither is chosen over `claude-sonnet-5-5`

#### Scenario: No list is reachable

- **WHEN** the LB list and route's cache are both unavailable
- **THEN** the alias resolves to its `pinned` id, and without one to the harness alias

### Requirement: The LB lists upstream Anthropic models through the pool

`GET /api/models/anthropic` MUST read upstream `/v1/models` through an Anthropic account that is not paused, deactivated, awaiting re-auth or exchange-uncertain, cache the ids for an hour, and serve the last good list marked stale when a refresh fails.

#### Scenario: One account is refused

- **WHEN** the first account answers 429
- **THEN** the next account is tried and its list is served
