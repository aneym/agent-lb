## Why

`fable-weekly-routing-preference` gave non-Fable Anthropic traffic a request-scoped
`burn_first` preference for accounts past the Fable weekly threshold — but only
fresh (unpinned) selections feel it. Sticky sessions reallocate solely under budget
pressure (95% primary / 100% secondary), so long-lived non-Fable sessions stay
pinned to under-threshold accounts and erode exactly the weekly headroom the
feature exists to preserve. Live evidence (2026-07-02, after one account crossed
the threshold at ~11:00Z): ~870 sticky Opus/Sonnet selections landed on
Fable-eligible accounts vs ~35 on the over-threshold drain account. The
second-lowest account (52% weekly remaining) is being burned toward the Fable
cutoff by non-Fable sticky traffic alone.

## What Changes

- New env setting: `ANTHROPIC_FABLE_STICKY_DRAIN_ENABLED` (default true), sibling
  to `ANTHROPIC_FABLE_ROUTING_ENABLED`.
- `LoadBalancer.select_account` gains `burn_first_sticky_drain: bool = False`,
  passed through to `_select_with_stickiness`.
- In the sticky branch: when the flag is set and the request carries a
  request-scoped burn set, a pinned non-`burn_first` account with a selectable
  `burn_first` target now triggers proactive reallocation **without** requiring
  budget pressure. Guards mirror the existing `budget_pressured` conditions
  (sticky kind in PROMPT_CACHE/STICKY_THREAD/CODEX_SESSION, strategy outside the
  drain family, pinned not RATE_LIMITED) so rate-limit grace/fallback semantics
  are untouched.
- `AnthropicProxyService._select_account` passes
  `burn_first_sticky_drain = bool(eligibility.burn_first_account_ids) and
settings.anthropic_fable_sticky_drain_enabled`. The burn set is only non-empty
  for non-Fable Anthropic traffic with Fable routing enabled, so Fable-class
  requests and other providers are structurally unaffected.

## Impact

- Existing non-Fable sticky sessions migrate to over-threshold accounts on their
  next request (one-time prompt-cache loss per session), then re-pin there —
  stable because a pinned `burn_first` account never re-triggers the drain.
- If no over-threshold account is selectable (rate-limited, unhealthy), the pin
  is kept — automatic fallback to prior behavior.
- Once every account is past the threshold, all pins are on `burn_first` accounts
  and the trigger is inert.
- Kill switch: `ANTHROPIC_FABLE_STICKY_DRAIN_ENABLED=false` restores prior
  sticky behavior exactly.
