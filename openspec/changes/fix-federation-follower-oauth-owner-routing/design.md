## Context

Federation followers pull owner account state every 60 seconds. The mirror contract carries access tokens for routing but deliberately excludes refresh tokens, so a follower cannot become the durable OAuth authority for a mirrored account. Before this change, the CLI and dashboard could still start and complete OAuth against a follower, producing a local credential the owner never received.

The MacBook is a follower of `studio`; `studio` is the account owner. Both run independent agent-lb services and databases.

## Goals / Non-Goals

**Goals:**
- Prevent follower-local OAuth from appearing successful.
- Direct the Anthropic auth CLI to the configured owner by default.
- Preserve explicit operator overrides and standalone/owner behavior.

**Non-Goals:**
- Forward an in-progress OAuth flow between instances.
- Add refresh tokens to federation mirrors.
- Change account ownership or transfer semantics.

## Decisions

### Gate all mutating OAuth routes at the API boundary

`POST /api/oauth/start`, `/complete`, and `/manual-callback` call a shared route-layer guard. When `federation_peer_url` is configured, the guard raises `DashboardConflictError` with code `oauth_owner_required` and the owner URL.

This uses the existing dashboard `409` error envelope and prevents both new flows and completion of stale follower-local flows. A service-layer `OAuthError` was considered, but that path maps to `502`, which incorrectly describes an ownership conflict as an upstream failure.

### Resolve the CLI default from launchd configuration

`scripts/anthropic-auth.sh` reads only `AGENT_LB_FEDERATION_PEER_URL` from the known launchd service environment. If present, that URL becomes the default `BASE_URL`; otherwise localhost remains the default. An explicitly supplied `BASE_URL` always wins.

A new unauthenticated settings endpoint was considered, but the value already exists locally and exposing a new endpoint would broaden the server contract unnecessarily. Transparent OAuth proxying was rejected because it would require cross-instance flow state and callback handling.

### Keep refresh tokens out of mirrors

The existing access-token-only mirror remains unchanged. Refresh credentials continue to move only through explicit ownership checkout/checkin operations.

## Risks / Trade-offs

- [The script assumes the canonical macOS launchd label] → Explicit `BASE_URL` remains available for other service managers or labels.
- [The dashboard reports the owner URL rather than redirecting automatically] → The failure is safe and actionable; automatic redirecting would complicate browser flow state and dashboard authentication.
- [A follower-local flow started before deployment cannot complete afterward] → All completion endpoints reject it consistently; restart the flow against the owner.

## Migration Plan

1. Deploy the same commit to follower and owner instances.
2. Restart both services.
3. Verify follower OAuth start returns `409 oauth_owner_required` with the owner URL.
4. Verify owner OAuth start still returns a browser authorization flow.
5. Verify the follower CLI resolves accounts through the owner and an explicit local `BASE_URL` still works.

Rollback is the prior commit; no database or schema rollback is required.

## Open Questions

None.
