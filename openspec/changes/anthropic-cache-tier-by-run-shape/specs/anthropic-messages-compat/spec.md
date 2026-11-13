## ADDED Requirements

### Requirement: Cache tier follows the run's shape
For Anthropic Claude Code requests that carry ephemeral cache breakpoints, the proxy MUST set one tier on every breakpoint in the request: the five-minute tier for one-shot runs (entrypoint `sdk-cli`) when `anthropic_one_shot_cache_5m_enabled` is on, and otherwise the one-hour tier when `anthropic_session_cache_1h_enabled` is on. The proxy MUST change only the `ttl` value of existing breakpoints and MUST NOT add, remove or reorder blocks.

#### Scenario: A long-lived session asks for five minutes
- **GIVEN** an `sdk-ts` request whose breakpoints ask for the five-minute tier
- **WHEN** the proxy forwards it
- **THEN** every breakpoint carries `ttl: 1h` and the extended cache TTL beta is set

#### Scenario: A one-shot run asks for one hour
- **GIVEN** an `sdk-cli` request whose breakpoints ask for the one-hour tier
- **WHEN** the proxy forwards it
- **THEN** every breakpoint carries `ttl: 5m`

#### Scenario: A breakpoint is not ephemeral
- **GIVEN** a request with a cache breakpoint whose type is not `ephemeral`
- **WHEN** the proxy forwards it
- **THEN** the request is forwarded unchanged
