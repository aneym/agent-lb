# Design — add-claude-session-map

## Session identity on the Anthropic path

Resolution order for the persisted `session_id` (first non-empty wins):

1. Client session id extracted from the request (finalized against the live
   capture recorded in `notes.md`, claude-cli/2.1.210): `metadata.user_id`
   parsed as a JSON-encoded object → its `session_id` key; legacy
   `..._session_<uuid>` suffix as fallback; then an explicit session header
   (Claude Code sends `X-Claude-Code-Session-Id`) via the existing
   `_anthropic_session_header()` extractor (`anthropic_service.py:1155`).
2. `null` — never synthesize a placeholder. The Responses path's synthetic
   `http_turn_<hex>` values are exactly what makes its `session_id` column
   useless for grouping; the Anthropic path must not repeat that.

`session_id` and the `_request_log_useragent_fields()` pair are computed once
at the top of `stream_messages()` (payload + inbound_headers are in scope for
all 7 `_persist_request_log` call sites) and passed through new keyword
parameters on `_persist_request_log`, which forwards them to
`repos.request_logs.add_log(...)` (already accepts all three).

## Sessions rollup

New sibling module `app/modules/sessions/` following the `request_logs`
module pattern (api/service/repository/schemas/mappers, router mounted in
`app/main.py`, `validate_dashboard_session` dependency).

- `GET /api/sessions?window_minutes=&limit=&offset=` — one row per
  `session_id`: request count, distinct models (with per-model request
  counts), input/output/cached token sums, cost sum, first/last `requested_at`,
  dominant `useragent_group`, provider(s), error count.
- `GET /api/sessions/{session_id}` — the same aggregate plus per-model
  breakdown rows and the most recent N request-log entries (reusing
  `RequestLogEntry`).
- Exclusion filter (portable SQL, no regex): `session_id IS NOT NULL AND
session_id NOT LIKE 'turn\_%' ESCAPE '\' AND session_id NOT LIKE
'http\_turn\_%' ESCAPE '\'` — matches `_SYNTHESIZED_TURN_STATE_PATTERN`
  (`app/modules/proxy/affinity.py:227`) without a regex dependency, keeping
  sqlite compatibility even though the live deployment is Postgres.
- Aggregation is SQL GROUP BY (deterministic work in code, not post-hoc
  Python loops), served by the new index.

## Index migration

One revision on head `20260703_000000_add_account_transfers`, repo naming
convention `YYYYMMDD_HHMMSS_add_request_logs_session_time_index.py`, guarded
by inspector checks like `20260602_070000_add_request_log_useragent_fields.py`,
creating `idx_logs_session_time (session_id, requested_at DESC)` with a
symmetric downgrade.

## Frontend

New `/sessions` route + `NAV_ITEMS` entry. `frontend/src/features/sessions/`
mirrors the reports feature for data-fetch (single aggregate GET via
`lib/api-client` + zod schemas, `useQuery` with 60s refetch) and the
`recent-requests-table` component for the table treatment. Formatting comes
from `utils/formatters.ts` (`formatTokensWithCached`, `formatCurrency`,
`formatModelLabel`, `formatDateTimeInline`). Drilldown is a detail panel/dialog
on row click fetching `GET /api/sessions/{id}`.

## Explicitly out of scope (v1)

- Agent/seat attribution below the session level: the LB sees models, not
  subagent identities. Per-model breakdown is the proxy for seat usage
  (`gpt-5.6-sol-*` = implementer/verifier seats). True agent attribution
  requires client-side transcript correlation — backlogged.
- Backfill of historical Anthropic rows (no session identity was captured;
  nothing to backfill from).
- Retagging the Responses path's synthetic turn ids.
