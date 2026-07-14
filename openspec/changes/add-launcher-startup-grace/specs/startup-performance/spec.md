# startup-performance Delta

## ADDED Requirements

### Requirement: Interactive launcher waits for a loaded local service during cold startup

After the local candidate's fast readiness probe fails with a connection-level
unreachable error, the interactive Claude launcher MUST check whether the
configured agent-lb launchd job is loaded. When loaded, it MUST poll
`/health/ready` at one-second intervals for at most
`CLAUDE_LB_STARTUP_GRACE_SECONDS`, defaulting to 90 seconds. A zero grace MUST
disable this wait. The launchd check MUST be bounded to approximately one
second. Remote candidates, HTTP responses, read timeouts, and the successful
first-probe path MUST retain their existing behavior.

#### Scenario: Loaded local service becomes ready during grace

- **WHEN** the local readiness connection is refused
- **AND** the configured launchd job is loaded
- **AND** `/health/ready` succeeds before the startup grace expires
- **THEN** the launcher selects the local candidate
- **AND** it prints one waiting status and one ready status

#### Scenario: Grace is disabled

- **WHEN** `CLAUDE_LB_STARTUP_GRACE_SECONDS=0`
- **THEN** a failed local fast probe falls through without checking launchd or polling

#### Scenario: Healthy and remote candidates retain the fast path

- **WHEN** the first local readiness probe succeeds
- **THEN** the launcher does not inspect launchd or enter startup polling
- **AND WHEN** a remote candidate fails
- **THEN** the launcher does not apply local startup grace
