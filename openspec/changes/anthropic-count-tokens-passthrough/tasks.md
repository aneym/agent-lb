## 1. Service passthrough

- [x] 1.1 Add `AnthropicProxyService.count_tokens` that selects an active
  provider-matching account (no sticky key; dedicated cooldown-free quota
  key), refreshes the bearer token, builds the standard Anthropic headers
  (OAuth beta included), and POSTs the raw body to
  `<upstream base>/v1/messages/count_tokens` with a 30s timeout.
- [x] 1.2 Return upstream status + body verbatim via
  `AnthropicCountTokensResult`; map transport failures to
  `AnthropicProxyError(502)`.
- [x] 1.3 Extract `_upstream_base_url` so messages and count_tokens share the
  provider base-URL selection; add the `_open_count_tokens_response` seam
  mirroring `_open_upstream_response`.

## 2. Route

- [x] 2.1 Add `POST /messages/count_tokens` on `v1_router` with
  `validate_proxy_api_key` + `validate_model_access`, no
  `_enforce_request_limits` reservation, and Anthropic error envelopes on
  missing model or selection failure.

## 3. Tests

- [x] 3.1 Route-level unit tests: forwarded upstream response (not 405),
  upstream error envelope passthrough, selection-failure envelope, missing
  model, and reservation/release paths untouched.
- [x] 3.2 Integration tests: verbatim forwarding with Authorization swap and
  beta header, no request-log/cooldown writes on success, and no account
  status/cooldown perturbation on upstream 429.

## 4. Spec Delta

- [x] 4.1 Add
  `openspec/changes/anthropic-count-tokens-passthrough/specs/account-routing/spec.md`.
- [x] 4.2 `npx --yes @fission-ai/openspec@latest validate --specs`.
