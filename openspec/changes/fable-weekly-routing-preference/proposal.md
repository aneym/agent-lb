## Why

Anthropic constrains Claude Fable usage per account: Fable is only served while the
account has weekly headroom (operator-stated policy: Fable stops being available
once ~50% of the weekly limit is consumed; upstream also tracks a dedicated
Fable-scoped weekly limit in `/api/oauth/usage` `limits[]`, which agent-lb does not
yet ingest). Today the balancer is Fable-blind twice over:

1. Fable requests can route to accounts past the weekly threshold, where upstream
   will refuse or degrade them.
2. Worse, the `usage_weighted` strategy prefers the **lowest** weekly-usage
   accounts for ALL Anthropic traffic — so Haiku/Sonnet/Opus requests
   systematically burn exactly the accounts that still have Fable headroom.

## What Changes

- New env settings: `ANTHROPIC_FABLE_ROUTING_ENABLED` (default true) and
  `ANTHROPIC_FABLE_WEEKLY_MAX_USED_PERCENT` (default 50.0).
- Model classification: `claude-fable-*` (case-insensitive substring `fable`)
  is Fable-class.
- **Hard preference for Fable requests**: `_provider_quota_eligibility` now also
  reads weekly (secondary) usage and, for Fable-class requests, keeps only
  accounts under the threshold. If that leaves zero accounts (but cooldown-
  eligible accounts exist), it falls back to the unfiltered pool with a warning —
  upstream remains the authority, so Fable never hard-fails purely on the local
  threshold.
- **Inverse soft preference for non-Fable requests**: accounts at/over the
  threshold are stamped `burn_first` for that selection (request-scoped,
  post-cache, never persisted), so the existing routing-policy tier drains them
  before under-threshold accounts. Accounts whose stored policy is `preserve`
  (or already `burn_first`) are not re-stamped — operator intent wins.
- `LoadBalancer.select_account` gains `burn_first_account_ids`; the stamp is
  applied in `_build_states` on cloned per-request state, so the 5s selection
  cache is unaffected.
- `/api/accounts` rows gain `fableEligible` (bool for Anthropic accounts —
  weekly used below threshold; null for other providers).

## Impact

- Fable requests land on accounts that can actually serve them; other Anthropic
  traffic preserves Fable headroom instead of eroding it.
- Sticky sessions: a Fable session pinned to an account that crosses the
  threshold mid-conversation re-routes on its next request (prompt-cache loss on
  that one session; correctness preserved).
- `check_opportunistic_admission` is intentionally unchanged (it admits traffic
  onto an already-chosen account rather than choosing between accounts).
- Follow-up (separate change): ingest the upstream Fable-scoped weekly limit
  (`limits[] kind=weekly_scoped scope.model "Fable"`) as an additional quota and
  key eligibility off the exact scoped percent instead of overall weekly usage.
