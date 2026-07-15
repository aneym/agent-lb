## ADDED Requirements

### Requirement: Bridged Messages requests persist client session identity

Request-log rows written by the HTTP-bridge path for Anthropic-shaped
`/v1/messages` requests (Sol model aliases) MUST persist the client session
id resolved from the original inbound request (metadata/session header) when
present, in preference to the synthesized turn-state id. Sticky-key
derivation, turn-state affinity bookkeeping, and previous-response ownership
memory MUST be unaffected. Requests without client session identity MUST keep
the existing synthesized-id behavior.

#### Scenario: Sol seat request logs the coordinator session id

- **WHEN** a Claude Code subagent sends `/v1/messages` with a Sol alias model
  and `X-Claude-Code-Session-Id` (or metadata session identity)
- **THEN** the persisted request-log row stores that session id, and the
  request appears under that session in the sessions rollup

#### Scenario: Codex-shaped bridge traffic is unchanged

- **WHEN** a bridged request carries no Anthropic client session identity
- **THEN** the persisted row keeps the synthesized turn-state id and remains
  excluded from the sessions rollup

### Requirement: Sessions API exposes per-session analytics aggregates

The sessions API MUST expose a per-session analytics resource providing:
time-bucketed series (adaptive bucket size, per bucket per model with
reasoning effort: requests, output tokens, cached input tokens, cost), seat
totals grouped by model and reasoning effort, and fixed-bin latency and
tokens-per-request distributions. The sessions list MUST provide a compact
per-session request-count sparkline series. Aggregation MUST occur in SQL
under the same eligibility rules as the rollup, and the analytics resource
MUST require dashboard auth and return 404 for unknown sessions.

#### Scenario: Analytics series covers the session window

- **WHEN** the analytics resource is requested for a session with requests
  spanning the window
- **THEN** the response contains bucketed series entries whose per-model
  values sum to the session's totals for the covered fields

#### Scenario: Seat totals group by model and effort

- **WHEN** a session contains requests from the same model at different
  reasoning efforts
- **THEN** the seats collection reports separate entries per (model, effort)
  pair with their own request, token, and cost totals
