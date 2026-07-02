## ADDED Requirements

### Requirement: Upstream overload responses fail over across accounts

The Anthropic messages path MUST treat an upstream 529 (overloaded) response as a per-account transient failure: the serving account is excluded for the request and selection retries on another account within the existing per-wake selection-attempt budget, after a short jittered backoff. A 529 MUST NOT record a quota cooldown and MUST NOT affect account error-health beyond the existing per-request error recording. Each 529 attempt MUST persist its request-log row with the upstream_529 error code. When the attempt budget exhausts with the last failure being a 529, the request MUST fail promptly (no reset-wait hold without a known reset) and surface the Anthropic-native overloaded error type with status 529.

#### Scenario: A browned-out account fails over to a healthy one

- **GIVEN** two eligible Anthropic accounts where the first returns 529 "Overloaded" and the second serves normally
- **WHEN** a messages request is routed
- **THEN** the request is served by the second account with a 200
- **AND** no quota cooldown is recorded for the account that returned 529
- **AND** a request-log row records the upstream_529 failure for the first account

#### Scenario: Full overload outage fails fast

- **GIVEN** every eligible Anthropic account returns 529
- **WHEN** a messages request is routed
- **THEN** the request fails after the bounded attempt budget without holding
- **AND** the surfaced error carries the overloaded error type

#### Scenario: Backoff is short and bounded

- **GIVEN** an upstream 529 with remaining attempt budget
- **WHEN** the path retries on the next account
- **THEN** the retry waits a short jittered backoff (sub-second initial, capped)
- **AND** no backoff runs after the final attempt
