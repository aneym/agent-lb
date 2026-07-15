# Tasks — add-claude-session-map

## 1. Session capture (backend)

- [x] 1.1 Finalize the session-id extraction rule from the live Claude Code
      request capture (metadata.user_id JSON `session_id` key, legacy
      suffix, `X-Claude-Code-Session-Id` header) and record the evidence in
      notes.md.
- [x] 1.2 Compute session id + useragent fields once in `stream_messages()`;
      add `session_id`/`useragent`/`useragent_group` parameters to the
      Anthropic `_persist_request_log` and thread them through all 7 call
      sites into `add_log`.
- [x] 1.3 Expose `sessionId` on `RequestLogEntry` (schemas.py + mappers.py).
- [x] 1.4 Alembic migration on head `20260703_000000_add_account_transfers`
      adding `idx_logs_session_time (session_id, requested_at DESC)` with
      inspector guards and symmetric downgrade.
- [x] 1.5 Regression tests: extraction unit tests (metadata/header/absent →
      null, never synthesized) and an integration test asserting the
      Anthropic proxy route persists session_id + useragent on the logged row.

## 2. Sessions rollup API (backend)

- [x] 2.1 New `app/modules/sessions/` module (api/service/repository/schemas/
      mappers) mounted in `app/main.py` behind `validate_dashboard_session`.
- [x] 2.2 `GET /api/sessions` list aggregate with synthetic-turn-id exclusion
      and window/limit/offset params.
- [x] 2.3 `GET /api/sessions/{session_id}` detail (aggregate + per-model
      breakdown + recent `RequestLogEntry` rows).
- [x] 2.4 Integration tests at the route level: grouping, exclusion of
      `http_turn_*`/`turn_*` and null session ids, token/cost sums, window
      filtering, auth dependency.
- [x] 2.5 Short-link redirect `GET /s/{session_id_prefix}`: resolve a unique
      eligible session by id or unambiguous prefix (min 8 chars), 302 to the
      dashboard sessions deep link; 404 on unknown, 409-style dashboard error
      on ambiguous prefix. No auth on the redirect itself (target dashboard
      enforces auth). Route-level tests.

## 3. Dashboard (frontend)

- [x] 3.1 `frontend/src/features/sessions/` (schemas.ts, api.ts, hooks,
      components) following the reports data-fetch pattern.
- [x] 3.2 Sessions list page: session, client group, models, requests,
      tokens (cached split), cost, first/last activity; row click opens
      detail with per-model breakdown + recent requests.
- [x] 3.3 Route `/sessions` + `NAV_ITEMS` entry.
- [x] 3.4 Component/hook tests per existing feature conventions; `npm run
  build` (vite) passes. (Verifier-reproduced: 10 focused tests, full
      suite 602 passing, vite build clean.)
- [x] 3.5 Deep link: `/sessions?session=<id>` auto-opens that session's
      detail view (used by the short-link redirect and status line).
      (Implemented by frontend lane; verifier confirmed close-param
      preservation in code.)

## 4. Status line

- [x] 4.1 `~/.claude/statusline-small.sh`: replace the trailing raw
      session_id segment with `http://127.0.0.1:2455/s/<first-8>` (only
      after the redirect route is live). (Smoke-tested against a sample
      statusline payload.)

## 5. Validation & ship

- [x] 5.1 `ruff check app clients` clean; targeted `uv run pytest` suites
      pass (71 backend focused; frontend full suite 602);
      `openspec validate add-claude-session-map --strict` passes.
- [x] 5.2 Migration applied by the service's startup migration runner on
      kickstart restart (`launchctl kickstart -k gui/501/com.aneyman.agent-lb`,
      healthy in ~5s).
- [x] 5.3 Live exercise: coordinator session 588bfb7a appears in
      `GET /api/sessions?windowMinutes=60` (claude-cli group, fable model,
      cost + cached split populated); `/s/588bfb7a` 302s to
      `/sessions?session=<full id>`; the SPA route serves 200 HTML (page
      render covered by component tests; no authenticated browser pass).
- [x] 5.4 Verifier seat closeout: SHIP-WITH-FIXES, all three findings fixed
      and re-gated (602 frontend tests, build clean); commit + push per
      standing auto-publish.
