## ADDED Requirements

### Requirement: Declarative reserve state
The route CLI SHALL compute reserve state from `policy.reserve`, returning null when the policy is absent. State SHALL contain `pool`, `active`, `admitted`, `eligible`, `headroom`, and `why`. The reserve SHALL activate when a present eligible account count is below `min_eligible` or a present headroom percentage is below `headroom_min_percent`; missing values SHALL NOT activate it. Admission SHALL depend on membership in `admit_classes`.

#### Scenario: Last Claude account
- **WHEN** `anthropic-general` has one eligible account and 50% headroom under the specified reserve policy
- **THEN** state is active with reason `1 eligible account (keep 2)`
- **AND** `plan` is admitted while `review` is not

#### Scenario: Low headroom
- **WHEN** three accounts are eligible but headroom is 35% under the specified reserve policy
- **THEN** state is active with reason `headroom 35% (keep 40%)`

#### Scenario: Healthy or missing pool facts
- **WHEN** neither present pool fact is below its threshold
- **THEN** state is inactive and its reason is null

### Requirement: Reserve preserves fallback routing
For an active reserve and a non-admitted class, the shared class candidate walk SHALL move all routable entries on the reserved pool after all other routable entries, preserving relative order within each group. Each moved entry SHALL carry `reserved` with value `reserved for orchestrators: <why>`. Existing eligibility filters SHALL remain unchanged.

#### Scenario: Prefer another pool
- **WHEN** review can use Claude and Sol while the Claude reserve is active
- **THEN** Sol is first and Claude remains a fallback
- **AND** route menu reflects the same ordering without changing its output format

#### Scenario: Only reserved pool remains
- **WHEN** cross-vendor verification of OpenAI-authored work has only a Claude seat routable
- **THEN** the Claude seat is selected rather than rejected

### Requirement: Picks explain reserve decisions
JSON picks SHALL include top-level `reserve` with the state or null. A reserved chosen entry SHALL have a reason beginning `reserved for orchestrators: <why>; no other seat open`. Text picks SHALL add `reserve: <why>` when the reserve is active and the class is not admitted.

#### Scenario: Last-resort pick explanation
- **WHEN** a reserved entry is selected as the sole available pool choice
- **THEN** JSON includes reserve state and the required reason prefix
- **AND** text output includes the reserve explanation line
