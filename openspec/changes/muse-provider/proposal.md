# Muse as a usage-based agent-lb provider

## Why
Alex, 2026-10-16 ~15:00Z, relayed by cos (b-20261016150035-36849e3ab4e385fc): "no cap necessary, muse is usage based, it can use as much as it wants through rails agent-lb please. factory should build that out." Rails agents on the Pi harness (Sunday first) call Muse through agent-lb. The existing `/muse-api` passthrough forwards the client's own Meta token, so a Pi agent, which holds no Meta token, got 401 on every call (request logs 2026-10-16 15:00Z, sessions `pi-eval-*`).

## What Changes
- New route `/muse/v1` (`POST /responses`, `GET /models`) speaking the OpenAI Responses wire. agent-lb drops the client's credentials and authenticates with the Muse login on the serving host (macOS keychain item `ai.meta.dev.credentials`, field `api_key`), cached five minutes and re-read once on an upstream 401.
- `muse-latest` or an empty model resolves to the routing table's pinned `muse-latest` model.
- Contributor mode stays off: a `-contributor` model is refused with 400 `contributor_mode_refused`; a non-Muse model gets 400 `unknown_muse_model`; no login on the host gets 503 `muse_credential_unavailable`.
- No cap. Every call writes one request log row with provider `muse`, transport `muse-provider`, tokens, and the caller seat from `x-agent-lb-seat`, so usage is visible per agent.
- `/muse-api` (Muse Code passthrough) is unchanged.

## Impact
Affected code: app/modules/proxy/meta_service.py, app/main.py. Affected specification: muse-provider (new).
