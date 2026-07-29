# instance-federation — delta

## ADDED Requirements

### Requirement: Owner accepts per-instance usage reports

The federation router SHALL expose `POST /api/federation/usage-report`,
protected by the same federation Bearer token as `/mirror`. The request body
carries the reporting `instance_id` and a list of per-day rollups, each keyed
by (`day` in UTC, `account_id`) with `provider`, request count, input/output
tokens, cache-read tokens, total cost, distinct session count, and
`last_request_at`. The receiver MUST upsert each rollup keyed by
(`instance_id`, `account_id`, `day`), replacing counter values (reports are
absolute per-day aggregates, not deltas), and record a `reported_at`
timestamp. Re-delivery of the same report MUST be idempotent. A report from
an instance MUST NOT overwrite rows of a different instance.

#### Scenario: Rollup upsert is idempotent

- **GIVEN** a follower has reported day D for account A with 10 requests
- **WHEN** the same follower reports day D for account A again with 12 requests
- **THEN** the stored row for (follower, A, D) shows 12 requests
- **AND** no duplicate row exists

#### Scenario: Peer auth is required

- **WHEN** `POST /api/federation/usage-report` is called without a valid
  federation Bearer token
- **THEN** the response is `403` and nothing is stored

### Requirement: Follower pushes usage rollups after mirror pulls

A follower (instance with `federation_peer_url` and `federation_token`
configured) SHALL, after each successful mirror pull, compute per-day usage
rollups from its local `request_logs` grouped by (`account_id`, UTC day) over
a trailing window (default 7 days, configurable via
`AGENT_LB_FEDERATION_USAGE_WINDOW_DAYS`) and push them to the peer's
usage-report endpoint tagged with its `local_instance_id`. A usage-report
push failure MUST NOT fail or delay the mirror pull, MUST NOT mark accounts
unhealthy, and MUST be retried on the next cycle. Instances with no peer
configured MUST NOT push.

#### Scenario: Push failure does not degrade mirroring

- **GIVEN** a follower whose mirror pull succeeds
- **WHEN** the subsequent usage-report push fails (peer 5xx or unreachable)
- **THEN** mirrored accounts remain routable and the mirror cycle is
  considered successful
- **AND** the next cycle attempts the push again

#### Scenario: Rollups cover only the trailing window

- **GIVEN** a follower with requests logged 30 days ago and 2 days ago
- **WHEN** it pushes a usage report with the default window
- **THEN** the report contains the day from 2 days ago and not the day from
  30 days ago

### Requirement: Per-instance usage view

Every instance SHALL expose `GET /api/usage/instances` under dashboard
authentication. The response groups usage by instance id and MUST include:
the local instance's own rollup computed live from its local `request_logs`
(same grain and window), and all stored federation usage reports, each with
its `reported_at`. Per-instance entries MUST include per-day and per-account
breakdowns and totals (requests, input/output tokens, cost). The local
instance MUST NOT appear twice even if a stored report exists under its own
id (live computation wins).

#### Scenario: Owner sees the whole pool

- **GIVEN** an owner with local requests and stored reports from follower F
- **WHEN** a dashboard client calls `GET /api/usage/instances`
- **THEN** the response contains one entry for the owner's instance id
  (computed live) and one for F (from stored reports)

#### Scenario: Follower without reports sees itself

- **GIVEN** a follower that has received no usage reports
- **WHEN** a dashboard client calls `GET /api/usage/instances`
- **THEN** the response contains exactly its own live rollup
