# instance-federation — delta

## ADDED Requirements

### Requirement: Federation status endpoint

Every instance SHALL expose `GET /api/federation/status` under dashboard
authentication (passwordless on localhost in standard mode), independent of
the federation peer Bearer requirement, so a local operator or CLI can
inspect federation health without the peer secret. The response MUST include:
`local_instance_id`; whether a federation token is configured (never the
token value); the configured `peer_url` or null; mirror scheduler state
(enabled, interval seconds, last success timestamp, last attempt timestamp,
consecutive failure count, last error string or null); usage-report push
state (last success timestamp, last error string or null); and account counts
split into locally-owned and mirrored. The endpoint MUST work when federation
is unconfigured, reporting it disabled rather than erroring.

#### Scenario: Healthy follower

- **GIVEN** a follower whose last mirror pull succeeded
- **WHEN** a localhost client calls `GET /api/federation/status`
- **THEN** the response reports the local instance id, the peer URL, a
  non-null mirror last-success timestamp, zero consecutive failures, and a
  mirrored account count greater than zero

#### Scenario: Unconfigured instance

- **GIVEN** an instance with no `federation_peer_url` and no federation token
- **WHEN** a localhost client calls `GET /api/federation/status`
- **THEN** the response is `200` reporting federation disabled with null peer
  and mirror state

#### Scenario: Token value is never exposed

- **WHEN** any client calls `GET /api/federation/status`
- **THEN** the response contains no federation token value
