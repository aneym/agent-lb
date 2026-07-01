## Context

Anthropic message routing performs account selection before opening an upstream
request. If the selected account needs an OAuth token refresh and the refresh token
is no longer accepted, the failure occurs before the upstream response handling
loop. That can bypass the existing failover behavior for upstream 401, 403, and
429 responses.

Warmup and probe code may also wrap provider auth errors with operation-specific
prefixes, for example `auth_refresh_invalid_grant`. The canonical permanent error
table contains `invalid_grant`, but not every prefixed alias, so classification and
status selection must normalize before consulting that table.

The API-key admission path reserves usage before streaming Anthropic responses are
consumed. If the stream body later turns a proxy error into an SSE error event,
that stream-level error guard must also release the reservation when no successful
usage finalization happened.

## Goals / Non-Goals

**Goals:**

- Treat known prefixed refresh-auth aliases as the same canonical permanent failure.
- Ensure `RefreshError` itself canonicalizes known permanent refresh codes so
  downstream callers cannot accidentally treat `invalid_grant` as transient.
- Mark stale refresh-token accounts as reauth-required with the canonical reason.
- Keep Anthropic message requests moving to another eligible account when refresh
  fails before upstream I/O.
- Release API-key usage reservations for streaming Anthropic proxy errors.
- Preserve the final error behavior when no eligible account remains.

**Non-Goals:**

- Reactivate canceled vendor subscriptions or change billing/subscription state.
- Change upstream Anthropic error semantics after all candidate accounts fail.
- Add new account credentials or store new secrets in source-controlled files.

## Decisions

- Canonicalize only recognized `auth_refresh_` aliases. Unknown prefixed codes keep
  their original value so transient or future errors are not accidentally treated as
  permanent.
- Apply canonicalization in the balancer permanent-failure helpers. This keeps
  status selection, persisted reasons, proxy marking, and account-manager updates
  consistent.
- Apply canonicalization inside `RefreshError` construction as the strongest
  invariant, while retaining defensive reclassification at key catch sites.
- Catch refresh-derived `AnthropicProxyError` instances inside the Anthropic
  message attempt loop. The failed account is already marked by `_fresh_access_token`
  when the underlying refresh error is permanent; the loop records a request log and
  selects another account on the next attempt.
- Let the Anthropic streaming error guard release a reservation only when it catches
  and emits an `AnthropicProxyError`. Successful streams remain finalized by the
  proxy service, and release/finalize methods are idempotent for non-reserved rows.

## Risks / Trade-offs

- Refresh failures still consume one selection attempt. The existing bounded
  `_MAX_SELECTION_ATTEMPTS` prevents unbounded loops.
- If every eligible account has stale auth, the client still receives a 401 or
  no-available-accounts response. That is intentional because there is no healthy
  account to route to.
- Prefix normalization is deliberately narrow. A future provider prefix may need to
  be added if it does not follow the `auth_refresh_` form.
- Streaming reservation cleanup now has two possible callers on error paths, but
  reservation settlement is guarded by the `reserved` status and is safe to retry.

## Migration Plan

Deploy the code with tests, restart the local `com.aneyman.agent-lb` service, and
smoke `/v1/messages`. Existing stale accounts are corrected lazily when refresh is
attempted, and can also be reauthenticated one by one through the existing OAuth
account flow.
