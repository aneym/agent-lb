# Context

## Verified facts (2026-07-13)

- MacBook instance: `AGENT_LB_FEDERATION_PEER_URL=https://studio.tailf266ac.ts.net:2455`,
  set only in the `com.aneyman.agent-lb` launchd service `EnvironmentVariables`
  (`launchctl print gui/$(id -u)/com.aneyman.agent-lb`) — not exported to an
  interactive shell, and not present in the repo's `.env`/`.env.local` (both
  empty on this checkout). Mirror pull runs every 60s
  (`AGENT_LB_FEDERATION_MIRROR_INTERVAL_SECONDS=60`).
- `studio` has no `federation_peer_url` configured — it is the owner.
- `FederationMirrorAccount` (`app/modules/federation/schemas.py`) intentionally
  has no `refresh_token` field: "NEVER carries a refresh token". This is
  provider-agnostic (has a `provider` field), so the fix applies to any
  provider's OAuth start, not just Anthropic.
- `app/core/exceptions.py::DashboardConflictError` (409) plus the global
  handler in `app/core/handlers/exceptions.py` already gives every dashboard
  route a typed `{"error": {"code", "message"}}` 409 envelope for free —
  just raise it, no new response schema. This is a cheaper, more correct fit
  than the OAuth service's `OAuthError` path (which `oauth/api.py` hardcodes
  to `502` regardless of the exception's own `status_code`), so the gate
  lives in `app/modules/oauth/api.py` as `_require_oauth_owner()`, called at
  the top of `start_oauth`, `complete_oauth`, and `manual_callback` — before
  their `try` blocks, since `manual_callback`'s catch-all `except Exception`
  would otherwise swallow it into a generic 500.

## Why the script reads the launchd env instead of a new endpoint

The script talks to the *local* instance's dashboard API and has no
authenticated session by design (it is a bare `curl` wrapper). Adding a new
unauthenticated endpoint just to expose `federation_peer_url` (itself
non-secret) would be a second server-side change for a value that already
lives in a configuration source the script can already reach on the same
machine. `launchctl print` on the known service label
(`com.aneyman.agent-lb`, already hardcoded the same way in
`scripts/install-service.sh`) was the lowest-footprint option; only the
`AGENT_LB_FEDERATION_PEER_URL` line is read, never `AGENT_LB_FEDERATION_TOKEN`.

## Scope not covered

- No transparent server-side forwarding/proxying of OAuth from follower to
  owner was implemented — the follower's dashboard OAuth start simply fails
  visibly per the brief ("unless a clean transparent forwarding mechanism
  already exists and can be implemented minimally"); none existed, and
  building one (proxying the whole browser/device flow across instances)
  is out of scope for this fix.
- Instances without the `com.aneyman.agent-lb` launchd service (e.g. running
  the server directly, or on a different machine/label) fall back to
  `http://127.0.0.1:2455`, matching prior behavior — unchanged for those
  setups.
