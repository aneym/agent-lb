## Why

A federation follower instance (`AGENT_LB_FEDERATION_PEER_URL` set, e.g. the
MacBook mirroring `studio`) never receives refresh tokens for the accounts it
mirrors (`app/modules/federation/schemas.py::FederationMirrorAccount` has no
`refresh_token` field by design). Today, `scripts/anthropic-auth.sh` and the
dashboard's OAuth-start flow both default to talking to the local instance.
On a follower that writes a brand-new, refresh-token-bearing credential that
the owner (studio) never sees and that the next mirror pull overwrites or
leaves orphaned — the operator believes they logged in, but the credential is
inert. The user expects studio to remain the sole OAuth authority.

## What Changes

- `scripts/anthropic-auth.sh` defaults `BASE_URL` to the configured
  federation peer/owner URL (read from the local launchd service
  environment) when running on a follower, instead of always defaulting to
  `http://127.0.0.1:2455`. An explicit `BASE_URL` env var still wins. A
  standalone/owner instance has no peer URL configured, so its behavior is
  unchanged.
- The dashboard OAuth routes (`POST /api/oauth/start`, `/complete`,
  `/manual-callback`) now fail fast with a typed `409 oauth_owner_required`
  error carrying the owner's URL when `federation_peer_url` is configured,
  instead of silently completing the flow and persisting an orphaned local
  credential. The gate lives in `app/modules/oauth/api.py` (route layer) and
  reuses the existing `DashboardConflictError` → 409 envelope convention
  already used elsewhere in the dashboard API, rather than the OAuth
  service's `OAuthError` → 502 path.

## Impact

- Affected: `scripts/anthropic-auth.sh`, `app/modules/oauth/api.py`.
- Owner/standalone instances (no `federation_peer_url`): no behavior change.
- Follower instances: the CLI script now targets the owner by default; all
  three dashboard OAuth endpoints return a `409 oauth_owner_required` with an
  actionable owner URL instead of accepting new logins locally.
- No schema, migration, or routing changes.
