## Why

Fable routing keys off overall weekly (`seven_day`) usage with a 50% heuristic,
but upstream tracks a **dedicated Fable-scoped weekly limit** that agent-lb
discards: `api/oauth/usage` `limits[]` contains
`{kind: "weekly_scoped", group: "weekly", percent, severity, resets_at,
scope: {model: {display_name: "Fable"}}, is_active}` (raw payload captured
2026-07-02: an account at 62% overall weekly was at **81% Fable-scoped**).
The heuristic is a proxy for the wrong variable in both directions: an account
can have plenty of overall headroom but a nearly-exhausted Fable budget (81% vs
62% above), or cross 50% overall on non-Fable usage while its Fable budget is
barely touched (verified: Fable probe 200 at 58% overall used). The prior
change (`fable-over-threshold-probing`) already treats the heuristic as
unverified; this change ingests the authoritative signal, which the original
`fable-weekly-routing-preference` proposal named as its follow-up.

## What Changes

- `AnthropicOAuthUsagePayload` parses `limits[]` (kind, group, percent,
  severity, resets_at, scope.model.display_name, is_active) instead of
  dropping it.
- The usage refresh path stores the Fable-scoped weekly entry per account as
  additional usage under quota key `anthropic_fable_scoped_weekly`
  (used_percent = `percent`, reset_at from `resets_at`), refreshed on every
  usage fetch like the primary/secondary windows.
- Fable eligibility in `_provider_quota_eligibility` prefers the scoped
  signal: when an account has a scoped entry recorded within the last 6 hours,
  the account is Fable-eligible iff scoped `used_percent <
anthropic_fable_scoped_max_used_percent` (new setting, default 100.0) —
  regardless of overall weekly usage. Accounts without fresh scoped data keep
  the existing heuristic + probe-marker behavior unchanged.
- The non-Fable burn set gains the same upgrade: accounts whose fresh scoped
  percent is at/over the scoped threshold are burn-first targets; accounts
  without fresh scoped data fall back to the overall-weekly heuristic.
- `fableEligible` in the accounts API reflects the scoped signal when fresh
  scoped data exists, else the heuristic (mappers).

## Impact

- Routing decisions track Anthropic's actual Fable budget: accounts stop
  serving Fable exactly when their scoped budget exhausts (not at an arbitrary
  overall percentage), and accounts with drained overall-but-fresh-Fable
  budgets keep serving Fable.
- The 50% heuristic and the pulse probing remain as fallbacks for accounts
  whose usage payloads lack the scoped entry (e.g. plans that don't expose it).
- Storage: one additional-usage row per account per usage refresh cycle in
  which the scoped entry appears; same cadence as existing usage rows.
