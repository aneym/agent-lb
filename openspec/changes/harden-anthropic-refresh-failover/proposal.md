## Why

Anthropic OAuth refresh failures can surface as `invalid_grant` while provider
wrappers or tests may still construct `RefreshError(..., is_permanent=False)`.
Operation-prefixed forms such as `auth_refresh_invalid_grant` can also appear.
When those shapes are not canonicalized at the source, a stale Anthropic account
can remain routable and return a client-visible 401 before the proxy tries another
eligible Anthropic account.

## What Changes

- Canonicalize provider/operation-prefixed refresh failure codes before permanent
  failure classification, status selection, and deactivation reason selection.
- Make `RefreshError` canonicalize known permanent refresh codes so every caller
  sees a reliable `is_permanent` flag even when a wrapper passes `False`.
- Make Anthropic `/v1/messages` fail over when a selected account fails token
  refresh before an upstream request is opened.
- Release API-key usage reservations when streaming Anthropic responses convert a
  proxy failure into an SSE error event.
- Add regression coverage for the prefixed error classification and the
  externally failing Anthropic messages route.

## Capabilities

### New Capabilities

- None

### Modified Capabilities

- `account-routing`: permanent refresh-auth failures must leave the routing pool,
  including provider-prefixed aliases, and Anthropic message routing must try
  remaining eligible accounts before surfacing an auth failure.

## Impact

- Code: `app/core/balancer/logic.py`, `app/core/auth/refresh.py`,
  `app/core/anthropic/oauth.py`, `app/modules/accounts/auth_manager.py`,
  `app/modules/proxy/anthropic_service.py`, `app/modules/proxy/api.py`
- Tests: auth refresh classification, Anthropic OAuth refresh errors, load
  balancer permanent failure handling, Auth Guardian cache invalidation,
  Anthropic `/v1/messages` failover, and Anthropic streaming error cleanup
- Runtime: restart `com.aneyman.agent-lb` after validation so local Claude traffic
  uses the hardened routing behavior.
