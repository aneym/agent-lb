# Add Claude session map

## Why

Operators can see individual request-log rows but cannot answer "what did this
Claude Code session use?" — which models/agent seats it dispatched, how many
tokens and dollars it spent, over what time span. The `request_logs.session_id`
column exists but is never populated on the Anthropic `/v1/messages` path, so
Claude Code traffic (the majority consumer) has no session identity at all,
and no aggregate view exists for any path. This picks up the spend-origin
analytics backlog noted in `openspec/specs/proxy-runtime-observability/context.md`.

## What Changes

- Persist a stable session id and user-agent metadata on `request_logs` for
  every Anthropic-path (`/v1/messages`, including GLM) request-log row.
- Expose `session_id` through the dashboard request-log API.
- Add a sessions rollup API: list sessions (request count, models used,
  token/cost totals, first/last activity, client group) and a per-session
  detail (per-model breakdown plus recent requests). Synthetic per-request
  turn ids from the Responses path are excluded from the rollup.
- Add a `(session_id, requested_at)` index migration on `request_logs`.
- Add a Sessions page to the dashboard (list + drilldown).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: Anthropic-path request logs carry session id
  and user-agent metadata; a sessions rollup API aggregates request logs by
  session.
- `frontend-architecture`: dashboard gains a Sessions page fed by the sessions
  rollup API.

## Impact

- `app/modules/proxy/anthropic_service.py` (`_persist_request_log` + its 7
  call sites inside `stream_messages`).
- `app/modules/request_logs/` (expose `sessionId`), new `app/modules/sessions/`.
- `app/db/alembic/versions/` (one index migration on head
  `20260703_000000_add_account_transfers`).
- `frontend/src/App.tsx`, `app-header.tsx` nav, new `frontend/src/features/sessions/`.
- Backend + frontend regression coverage at the API route and page level.
