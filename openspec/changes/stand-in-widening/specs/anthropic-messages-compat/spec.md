## ADDED Requirements

### Requirement: Review lanes retain their vendor
Automatic launcher tagging SHALL exclude kinds-file lane or title and HERDR_TAB_TITLE containing review, verif or audit case-insensitively, and registry kind review. Explicit AGENT_LB_INTENT SHALL take precedence in the launcher. The server SHALL refuse stand-ins for tagged lanes containing review, verif or audit and log stand_in_refused_review_lane.

#### Scenario: Explicitly tagged review lane
- **GIVEN** a main-thread request tagged lane-tab with lane Review-money
- **WHEN** Anthropic returns 429
- **THEN** the handler preserves the Anthropic error and does not invoke the stand-in

### Requirement: Stable half rollout and visible tags
The launcher SHALL support half using even SHA-256 hashes of HERDR_TAB_ID plus configured canary tabs and lanes, excluding review lanes. Claude and ccgpt launchers SHALL use the same tagging rule. Successful banners SHALL include resolved stand-in intent and lane or stand-in: off on stderr. Dry-run tags SHALL be printed on stderr, not stdout.

#### Scenario: Repeat half-stage launch
- **GIVEN** half rollout and a non-review tab outside the canaries
- **WHEN** it launches repeatedly
- **THEN** its stable hash selects the same tagging result each time

### Requirement: Internal routing headers stop at the LB
The LB SHALL consume x-agent-lb-intent and x-agent-lb-lane locally and drop both from upstream requests.

#### Scenario: Tagged request goes upstream
- **GIVEN** a tagged request carrying intent and lane headers
- **WHEN** the LB constructs upstream headers
- **THEN** neither internal header reaches the provider

### Requirement: Forced swap is checked without live traffic
The forced-swap script SHALL use the isolated LB test harness with fake upstream responses, exit zero only after proving a tagged main-thread 429 swaps to sol-latest-high with a swap header and the next recovered request returns to Anthropic without that header, and print one result line per step.

#### Scenario: Upstream recovers
- **GIVEN** the fake Anthropic upstream returns 429 then succeeds
- **WHEN** the script runs
- **THEN** it proves the swap and immediate return without contacting the live LB
