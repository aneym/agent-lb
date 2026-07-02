## Why

Claude Code clients POST `/v1/messages/count_tokens` before sending large
requests. agent-lb registers `POST /v1/messages` but no count route, so those
requests fail with `405 Method Not Allowed` (logged as
`openai_error_response ... path=/v1/messages/count_tokens status=405`).
Token counting is quota-free upstream, so the proxy should forward it instead
of breaking clients that use it out of the box.

## What Changes

- Add `POST /v1/messages/count_tokens` on the `/v1` proxy router. The route
  validates the proxy API key and per-key model access exactly like
  `/v1/messages`, then forwards the raw JSON body to the selected account's
  Anthropic-compatible upstream `/v1/messages/count_tokens` endpoint and
  returns the upstream status and JSON body verbatim (Anthropic error
  envelopes included).
- Add `AnthropicProxyService.count_tokens`: reuses the existing account
  selection (`_select_account`), token refresh, and header-building helpers
  (OAuth beta header included), with a probe-like 30s upstream timeout.
  Because counting is free and account-agnostic, selection skips sticky
  affinity and uses a dedicated quota key that never records cooldowns, and
  the path performs no api-key reservation, usage settlement, request-log, or
  account error-health writes. (Reservation settlement ordering is therefore
  moot: nothing is reserved.) Token-refresh failures still flow through the
  shared `_fresh_access_token` path, whose permanent-failure handling is
  endpoint-independent.
- Selection failures surface the existing Anthropic-style error envelope via
  `_anthropic_error_response` / `_anthropic_proxy_error_response`.

## Capabilities

### Modified Capabilities

- `account-routing`: the Anthropic Messages proxy surface gains a quota-free
  `count_tokens` passthrough that may be served by any active
  provider-matching account without perturbing message routing, budgets, or
  account health.

## Impact

- **Code**: `app/modules/proxy/api.py` (new route),
  `app/modules/proxy/anthropic_service.py` (new `count_tokens` method,
  `_open_count_tokens_response` seam, shared `_upstream_base_url` helper).
- **Tests**: `tests/unit/test_anthropic_proxy_api.py` (route-level
  passthrough, reservation-path untouched, selection-failure envelope,
  missing-model validation), `tests/integration/test_anthropic_proxy.py`
  (end-to-end forwarding with header swap and no usage/health writes;
  upstream error envelope passthrough without cooldown writes).
- **API surface**: one new endpoint; `/v1/messages` behavior is unchanged.
- **Operational**: no migration, no new configuration. Counting does not
  consume api-key budgets or account quota state.
