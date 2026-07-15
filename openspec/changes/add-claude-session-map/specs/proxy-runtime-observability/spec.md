## ADDED Requirements

### Requirement: Anthropic request logs persist client session identity

Request-log rows written by the Anthropic Messages path (including GLM
routing) MUST persist the client session id and user-agent metadata. The
session id MUST be resolved from client-supplied request identity (the
`metadata.user_id` session suffix or an explicit session header) and MUST be
`null` when the client supplies none — the proxy MUST NOT synthesize a
placeholder session id on this path. User-agent capture MUST match the
existing `useragent`/`useragent_group` semantics.

#### Scenario: Claude Code request persists its session id

- **WHEN** a `/v1/messages` request carries client session identity (per the
  captured Claude Code request shape)
- **THEN** the persisted `request_logs` row stores that session id verbatim
  and the request's `useragent`/`useragent_group`

#### Scenario: Request without session identity stays null

- **WHEN** a `/v1/messages` request carries no session identity
- **THEN** the persisted row stores `session_id = null` and is excluded from
  the sessions rollup

### Requirement: Sessions rollup API aggregates request logs by session

The dashboard API MUST expose a sessions rollup: a list endpoint returning
one aggregate per session id (request count, per-model request counts, token
sums with cached split, cost sum, error count, first/last activity, dominant
client group) and a detail endpoint returning the aggregate plus per-model
breakdown and recent request-log entries. Both MUST require the dashboard
session auth used by other dashboard routers. The rollup MUST exclude rows
whose session id is `null` or matches the synthesized Responses turn-state
pattern (`turn_<hex32>` / `http_turn_<hex32>`).

#### Scenario: Session aggregates group by session id

- **WHEN** multiple request-log rows share a session id
- **THEN** the list endpoint returns one aggregate for that session with
  request count, model set, token and cost sums, and first/last activity

#### Scenario: Synthetic turn ids are excluded

- **WHEN** request-log rows carry synthesized `http_turn_<hex32>` or
  `turn_<hex32>` session ids
- **THEN** the sessions rollup omits them from list and detail responses

#### Scenario: Request-log entries expose session id

- **WHEN** the dashboard request-log list returns a row whose stored
  session id is non-null
- **THEN** the entry payload includes that session id

### Requirement: Session short links resolve to the dashboard session view

The server MUST expose `GET /s/{session_id_or_prefix}` which resolves a
rollup-eligible session by full id or unambiguous prefix (minimum 8
characters) and redirects to the dashboard sessions deep link for that
session. Unknown ids MUST return 404 and ambiguous prefixes MUST return an
error rather than redirecting to an arbitrary match. The redirect itself
carries no session data; the dashboard target enforces its own auth.

#### Scenario: Short link redirects to session detail

- **WHEN** `GET /s/<first 8 chars of an eligible session id>` matches exactly
  one session
- **THEN** the response is a redirect to the dashboard sessions view deep
  link for that session

#### Scenario: Unknown or ambiguous short links do not redirect

- **WHEN** the prefix matches zero eligible sessions, or more than one
- **THEN** the server returns an error response and does not redirect
