# Fail over when an account's plan lacks the requested model

## Why
On 2026-09-24 at 17:06Z one ChatGPT account's live plan dropped from pro to free
while its stored plan stayed pro. Every gpt-6-sol, gpt-6-terra, gpt-6.1-sol and
gpt-6-sol-pro request routed to it came back as HTTP 400 `invalid_request_error`
("The 'gpt-6-sol' model is not supported when using Codex with a ChatGPT
account."). The LB classified that as non-retryable and sent it straight to the
client, although a second Pro account was serving the same model: 48 gpt-6-sol
requests failed that way in one hour.

`agent-lb status` also reported 0/4 OpenAI accounts usable while a Pro account
was serving traffic, because the accounts API reports the missing five-hour
window as `null` and the CLI read that as malformed telemetry.

## Changes
- Recognize the upstream account-model rejection by its message, classify it as
  `account_model_unsupported`, fail the request over to another account on every
  transport (HTTP stream, websocket, HTTP bridge), and never penalize the
  account's health for it.
- Remember the (account, model) pair for 30 minutes; selection skips it for that
  model only. When no compatible account remains, keep routing so the client sees
  the upstream answer instead of a local error.
- `agent-lb status`: a `null` usage window is absent, not malformed.

## Compatibility
No schema, API or configuration changes. Other models keep routing to the
account. Security-work, file-pinned and previous-response ownership rules are
unchanged.
