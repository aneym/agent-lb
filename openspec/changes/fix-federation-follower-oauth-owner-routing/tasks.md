## 1. Script

- [x] 1.1 `scripts/anthropic-auth.sh`: add `_default_base_url` that reads
      `AGENT_LB_FEDERATION_PEER_URL` out of the `com.aneyman.agent-lb`
      launchd service environment (`launchctl print`) and falls back to
      `http://127.0.0.1:2455` when unset; `BASE_URL` env var still overrides.

## 2. Server

- [x] 2.1 `app/modules/oauth/api.py`: add `_require_oauth_owner()` that raises
      `DashboardConflictError(..., code="oauth_owner_required")` (409) with
      the owner URL when `get_settings().federation_peer_url` is set; reuses
      the existing dashboard-envelope exception-handler convention instead of
      a new response shape.
- [x] 2.2 Call the guard at the top of `start_oauth`, `complete_oauth`, and
      `manual_callback` (before their `try` blocks, so it isn't swallowed by
      `manual_callback`'s catch-all `except Exception`) — defense-in-depth
      across every dashboard OAuth entry point.

## 3. Tests

- [x] 3.1 `tests/integration/test_oauth_flow.py::test_oauth_endpoints_blocked_on_federation_follower`
      — asserts `409` + `oauth_owner_required` + owner URL in the message on
      all three routes, and that no flow is left pending in the store.
- [x] 3.2 Full existing `tests/integration/test_oauth_flow.py` suite stays
      green (owner/standalone path unaffected).

## 4. Validation

- [x] 4.1 `uv run pytest tests/integration/test_oauth_flow.py -q`
- [x] 4.2 `uv run ruff check app clients`
- [x] 4.3 `npx --yes @fission-ai/openspec@latest validate --specs --strict`
