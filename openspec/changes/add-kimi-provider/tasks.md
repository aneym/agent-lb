# Tasks

## Phase A — Moonshot API-key provider (unblocks benchmark gate)

- [ ] Add `MoonshotProvider` (GLM-pattern, Anthropic-native, API-key import),
      register it, and add `moonshot` upstream base-URL settings.
- [ ] Route `kimi-*` Messages requests through Moonshot-only account selection
      to the platform Anthropic endpoint; keep quota keys, request-log labels,
      and sticky-session keys provider-scoped.
- [ ] Relax the API-key import guard to accept `moonshot`; add a Moonshot
      probe model.
- [ ] Map upstream failures to status-specific cooldown windows honoring
      `Retry-After`, with burst suppression inside an open window.
- [ ] Focused backend coverage: provider registry, api-key import, `kimi-`
      prefix routing, provider-scoped cooldown/sticky keys, and the Kimi
      request-shape quirks (reasoning_content, empty text parts,
      tool_call_id chains).
- [ ] Import a real Moonshot API key, exercise a live `/v1/messages` with
      model `kimi-k3` through 127.0.0.1:2455, restart the live service.

## Phase B — Kimi subscription accounts (OAuth device flow)

- [ ] Implement RFC 8628 device-code login (start/poll/persist) for provider
      `moonshot` subscription accounts; store tokens encrypted with device-id
      metadata.
- [ ] Refresh with lead window + single-flight coalescing, extending the
      existing oauth-refresh-safety machinery.
- [ ] Send coding-agent identification headers (`X-Msh-*`) with a stable
      per-account device id on subscription-account upstream requests; route
      those accounts to the subscription base URL.
- [ ] Live-capture verification of header values, token lifetimes, and
      accepted model ids from a real kimi-cli session BEFORE enabling the
      subscription path by default.
- [ ] Subscription-path coverage: device-flow state machine (pending/
      slow_down/expired/denied), refresh races, fingerprint headers present,
      401-on-subagent behavior surfaced as account (not pool) failure.
- [ ] Add owner's Kimi subscription account via the login flow, balance across
      accounts if more than one, validate live routing + cooldown behavior.

## Closeout

- [ ] `uv run ruff check app clients` + relevant `uv run pytest` green;
      OpenSpec strict validation passes.
- [ ] Restart `com.aneyman.agent-lb` via kickstart, verify `/v1/messages`
      with `kimi-k3`, converge studio + laptop checkouts on origin/main.
- [ ] Update this checklist with proof, then verify + archive the change.
