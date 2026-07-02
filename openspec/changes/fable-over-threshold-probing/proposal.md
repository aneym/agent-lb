## Why

The Fable weekly threshold (50% used) is an operator-stated belief about when
Anthropic stops serving Fable-class requests — it has never been verified
upstream. Today the balancer hard-excludes over-threshold accounts from Fable
routing, so if Anthropic does not actually block (or blocks at a higher level),
we permanently strand real Fable capacity: an account that crosses 50% loses
all Fable traffic even though upstream might serve it fine. The operator asked
explicitly: "when we cross 50 on each of the accounts, we should keep trying
fable in case anthropic doesn't actually block usage."

## What Changes

- The account pulse additionally sends a tiny real `claude-fable-5` probe
  (`max_tokens` ~4, same machinery as `POST /api/accounts/{id}/probe`) to each
  routable Anthropic account whose weekly usage is at/over the Fable threshold,
  gated on `ANTHROPIC_FABLE_OVER_THRESHOLD_PROBE_ENABLED` (default true).
- Probe outcomes write a Fable-access marker (additional-usage style entry,
  quota key `anthropic_fable_access`):
  - 2xx → capable, expires after `ANTHROPIC_FABLE_PROBE_TTL_SECONDS`
    (default 43200 = two default pulse cycles);
  - 4xx model/permission refusal → refused, standing until the account's weekly
    (secondary) window resets (fallback 24h when unknown);
  - 429/5xx/network → inconclusive, marker unchanged.
- Fable eligibility in `_provider_quota_eligibility` becomes: all
  under-threshold accounts PLUS over-threshold accounts whose latest fresh
  marker is capable. Over-threshold accounts without a fresh capable marker
  stay excluded (safe default) until the next pulse verifies them.
- A Fable probe outcome MUST NOT change general account status, subscription
  ledger, or routing state — it only feeds the Fable-access marker.
- The existing full-pool fallback (all accounts over threshold) and the
  non-Fable drain preference are unchanged.

## Impact

- If Anthropic does not block Fable at 50%, accounts rejoin the Fable pool
  within one pulse cycle of crossing and capacity is not stranded; the real
  block level (if any) is discovered empirically and re-tested every cycle.
- If Anthropic does block, the refusal marker keeps live Fable traffic away
  until the weekly window resets, and probing (one tiny request per cycle)
  keeps checking after that.
- Probe cost: one ~4-token Fable request per over-threshold account per pulse
  cycle — negligible.
