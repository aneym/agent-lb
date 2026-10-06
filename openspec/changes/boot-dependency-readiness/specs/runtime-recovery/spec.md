## ADDED Requirements

### Requirement: Bounded credential-safe boot database wait

The runtime launcher SHALL wait for configured PostgreSQL readiness for at most AGENT_LB_DB_WAIT_SECONDS (default 900 seconds), with backoff capped at eight seconds. Each probe, including DNS resolution, SHALL be bounded by the remaining budget. It SHALL start the runtime after the budget expires. Database logs SHALL contain only the parsed host and port, never userinfo or query values. A parsing failure SHALL log only `db: unparseable url` and SHALL NOT log URL contents.

#### Scenario: Credentials contain URL delimiters

- **WHEN** a PostgreSQL URL contains userinfo with delimiter characters and query credentials
- **THEN** only the authority after the final userinfo separator supplies the probe host and port and no credentials appear in logs

#### Scenario: Database probe stalls

- **WHEN** the configured PostgreSQL probe or DNS lookup blocks
- **THEN** the launcher ends the probe within the remaining wait budget and starts the runtime after expiry

### Requirement: Watchdog dependency readiness fails closed

The watchdog SHALL NOT restart or kickstart an unhealthy runtime if database configuration cannot be read, its PostgreSQL URL cannot be parsed, its PostgreSQL probe is unavailable, or its probe fails. Only a successfully read no-database or SQLite configuration, or a successful PostgreSQL readiness probe, SHALL pass the dependency gate.

#### Scenario: Plist database lookup fails

- **WHEN** no database configuration is supplied by the environment and the plist lookup fails
- **THEN** readiness is unknown and the watchdog does not restart the runtime

### Requirement: Usage refresh cooldown cap includes jitter

The usage-refresh cooldown SHALL remain at or below 900 seconds including jitter.

#### Scenario: Maximum backoff receives jitter

- **WHEN** a usage-refresh failure reaches maximum backoff and receives positive jitter
- **THEN** its final cooldown does not exceed 900 seconds
