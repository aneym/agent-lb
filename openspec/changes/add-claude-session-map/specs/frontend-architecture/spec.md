## ADDED Requirements

### Requirement: Sessions page lists client sessions with per-session usage

The dashboard MUST provide a Sessions page at route `/sessions`, registered in
the header navigation, that lists sessions from the sessions rollup API. Each
row MUST show the session identifier, client group, models used, request
count, token totals with cached split, cost, and first/last activity, using
the shared formatter utilities. The page MUST follow the dashboard data-fetch
conventions (zod-validated `lib/api-client` calls via TanStack Query with
periodic refetch).

#### Scenario: Sessions list renders active sessions

- **WHEN** the sessions rollup API returns at least one session
- **THEN** the Sessions page renders one row per session showing client
  group, models, request count, token totals, cost, and last activity

#### Scenario: Session drilldown shows per-model breakdown

- **WHEN** the operator selects a session row
- **THEN** the page shows that session's per-model breakdown and its most
  recent request-log entries fetched from the session detail endpoint

#### Scenario: Deep link auto-opens a session detail

- **WHEN** the Sessions page loads with a `session` query parameter naming a
  known session id
- **THEN** that session's detail view opens automatically

#### Scenario: Rollup API failure surfaces an error state

- **WHEN** the sessions rollup API returns an error
- **THEN** the Sessions page shows an error state instead of stale or empty
  data presented as current
