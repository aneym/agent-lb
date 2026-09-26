## ADDED Requirements

### Requirement: Status advises on low recent Anthropic cache reads
The status CLI MUST report the cache-read ratio for successful non-warmup Anthropic requests in the trailing hour, calculated as cache-read tokens divided by the sum of uncached input, cache-creation, and cache-read tokens. It MUST emit an alert only when the ratio is strictly below 50 percent and the denominator is positive. It MUST report unknown, without an alert, when no qualifying traffic exists, the denominator is zero, or qualifying token telemetry is incomplete. The observation endpoint MUST use a database aggregate, require dashboard authentication, and MUST NOT call an upstream provider or change state. A failed cache observation MUST not turn an otherwise successful status snapshot into an observation failure.

#### Scenario: Low cache-read ratio
- **GIVEN** complete Anthropic usage in the trailing hour with cache reads below half of input tokens
- **WHEN** an operator runs `agent-lb status`
- **THEN** JSON and human output show a low-cache advisory

#### Scenario: No measurable traffic
- **GIVEN** no qualifying requests or no measurable input tokens
- **WHEN** an operator runs `agent-lb status`
- **THEN** the cache-read state is unknown and no alert is shown
