## ADDED Requirements

### Requirement: Pool routing never bills Anthropic extra-usage credits by default

Anthropic selection MUST exclude accounts whose primary window utilization is at
or above 100 percent, regardless of whether upstream would still serve requests
by billing extra-usage credits. When `ANTHROPIC_ROUTE_TO_EXTRA_USAGE` is false
(default) this exclusion MUST hold even when no other account is eligible: the
selection failure MUST surface the existing rate-limit envelope with the
earliest known reset time. When the setting is true, credit-billing accounts
MAY be selected only after every subscription-covered candidate is exhausted.

#### Scenario: Exhausted account with extra usage enabled is not selected

- **GIVEN** an Anthropic account at 100% primary utilization with `extra_usage.is_enabled`
- **AND** another account with subscription headroom
- **WHEN** a request is routed
- **THEN** the request is served by the account with subscription headroom

#### Scenario: Pool exhaustion waits instead of billing credits

- **GIVEN** `ANTHROPIC_ROUTE_TO_EXTRA_USAGE=false` and every Anthropic account at or above 100% primary utilization
- **WHEN** a request is routed
- **THEN** selection fails with the rate-limit envelope carrying the earliest reset time
- **AND** no request is forwarded to a credit-billing account

#### Scenario: Opt-in last resort

- **GIVEN** `ANTHROPIC_ROUTE_TO_EXTRA_USAGE=true` and every account at or above 100% primary utilization
- **WHEN** a request is routed
- **THEN** a credit-billing account may serve it

### Requirement: Pool-exhausted sessions wait for the window instead of dying

The Anthropic messages path MUST, when selection fails pool-wide with a known
earliest reset and `ANTHROPIC_POOL_EXHAUSTED_WAIT_ENABLED` is true (default),
hold streaming requests open instead of returning the rate-limit error: send
the stream headers, emit periodic SSE keepalive comments, and re-attempt
selection when the earliest reset passes (re-polling on a bounded cadence)
until an account frees or `ANTHROPIC_POOL_EXHAUSTED_WAIT_MAX_SECONDS`
(default 21600) elapses. On success the upstream stream is forwarded as usual;
on cap expiry or client disconnect the hold ends with the existing mid-stream
structured rate-limit error (cap) or a clean cancel (disconnect). Non-streaming
requests and disabled configuration MUST keep the existing immediate 429
envelope with retry timing.

#### Scenario: Agent session survives pool-wide window exhaustion

- **GIVEN** every eligible Anthropic account cooling down with a known earliest reset
- **WHEN** a streaming messages request arrives
- **THEN** the response opens with keepalive comments instead of a 429
- **AND** once the earliest window resets and an account frees, the request is served on it

#### Scenario: Wait cap surfaces the structured error

- **GIVEN** a held request whose wait exceeds the configured cap
- **WHEN** the cap elapses without any account freeing
- **THEN** the stream ends with the existing structured rate-limit error carrying reset timing

#### Scenario: Waiting can be disabled

- **GIVEN** `ANTHROPIC_POOL_EXHAUSTED_WAIT_ENABLED=false`
- **WHEN** selection fails pool-wide
- **THEN** the existing immediate rate-limit envelope is returned

### Requirement: Credit-billing responses trip an immediate cooldown

The proxy MUST inspect Anthropic rate-limit status response headers on proxied
responses and, when they indicate the unified limit is exhausted (upstream now
billing extra-usage credits), record the same quota cooldown an upstream 429
would record — so subsequent requests rotate to another account immediately
instead of waiting for the next usage refresh.

#### Scenario: First credit-billing response rotates the account

- **GIVEN** a session pinned to an account that crosses 100% primary utilization mid-window
- **WHEN** a response arrives whose rate-limit headers show the unified limit exhausted
- **THEN** a quota cooldown is recorded for that account until the window reset
- **AND** the session's next request is served by a different eligible account
