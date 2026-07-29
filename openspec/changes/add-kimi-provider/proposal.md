# add-kimi-provider

## Why
Kimi K3 (Moonshot AI, released 2026-07-16) is independently the strongest
frontend/creative-coding generator available (#1 Arena Frontend Code Elo) and
ships both an Anthropic-compatible pay-per-token API and an OAuth-authenticated
consumer subscription endpoint. Today neither can ride through agent-lb: Kimi
traffic would have to bypass the balancer entirely, losing account inventory,
sticky routing, request logs, quota cooldowns, and the seat-alias machinery the
rest of the pool uses. The owner wants Kimi subscriptions baked in end to end —
authentication, load balancing, and account management — so a `kimi-k3` seat
can be pinned in agent definitions exactly like existing Sol seats.

## What Changes
- Add a Moonshot provider (provider name `moonshot`) modeled on the GLM
  provider: Anthropic-Messages-native upstream, no request/response translation.
- Phase A (API key): import Moonshot platform API keys as accounts; route
  Anthropic-compatible `/v1/messages` requests whose canonical model starts
  with `kimi-` to `https://api.moonshot.ai/anthropic` using only Moonshot
  accounts.
- Phase B (subscription): add an RFC 8628 device-code OAuth flow for Kimi
  subscription accounts; store access/refresh tokens encrypted; refresh with a
  lead window and single-flight coalescing; route subscription-account traffic
  to `https://api.kimi.com/coding` with the required coding-agent
  identification headers (`X-Msh-*`) and a stable per-account device id.
- Keep Moonshot quota cooldowns, request-log provider labels, and
  sticky-session keys provider-scoped so they cannot collide with
  Anthropic/Claude or GLM routing.
- Map upstream failures to status-specific cooldown windows and honor
  upstream retry hints; suppress cooldown re-escalation for failure bursts
  inside an already-open window.
- Do not hardcode an exhaustive Kimi model catalog; unknown `kimi-*` models
  route by prefix so upstream model churn does not require a redeploy.

## Impact
- Operators can add both Moonshot API-key accounts and Kimi subscription
  accounts, and balance across several of either kind.
- A Claude Code agent definition can pin `model: kimi-k3` and be served by the
  local pool with the same sticky/cooldown behavior as other providers.
- Existing OpenAI/Codex, Anthropic/Claude, and GLM routing remains
  provider-filtered and unaffected.
- New capability spec `moonshot-provider` plus deltas to `account-routing`,
  `account-credential-import`, and `oauth-refresh-safety`.

## Non-goals
- No OpenAI-compatible (`/v1/chat/completions`) Kimi path: both chosen
  upstreams are Anthropic-Messages-native, and agent-lb's Messages route is
  the only consumer.
- No automatic purchase or tier management of Kimi memberships.
- No seat-table (ROUTING.md) edits in this change; routing-policy changes ship
  separately after the benchmark gate.
