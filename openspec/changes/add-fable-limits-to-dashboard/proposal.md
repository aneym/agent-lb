# add-fable-limits-to-dashboard

## Why

Limit windows are the operating signal the owner tracks most, and Anthropic's
dedicated Fable-scoped weekly limit is the scarcest one — the menubar already
surfaces it (focus ring, per-account availability), but the web dashboard did
not. The accounts view showed only Session/Week bars, the dashboard account
cards were not provider-aware, and the Fable-scoped quota appeared only as a
raw-keyed entry ("anthropic_fable_scoped_weekly") buried in the account
detail's additional-quotas list.

## What Changes

- Quota registry: `anthropic_fable_scoped_weekly` gains a display label,
  "Fable weekly (scoped)", so every API payload carries a human label.
- Shared frontend helper (`features/accounts/fable.ts`) derives the Fable
  window (remaining %, ISO reset, eligibility) from `additionalQuotas` +
  `fableEligible`; `fableEligible` added to the account zod schema.
- Accounts page: list rows show a third "Fable" meter for Anthropic accounts
  reporting the scoped window; the account detail usage panel promotes Fable
  into the main usage grid (labeled "Fable · out" when ineligible) and drops
  the duplicate from the additional-quotas list.
- Dashboard: account cards render Session/Weekly/Fable bars for Anthropic
  accounts (provider-aware labels); a "Fable runway (weekly scoped)" stat
  tile reports the mean Fable remaining % across routable Anthropic
  reporters, with eligible/total account counts (menubar parity: exhausted
  routable accounts stay in the denominator).

## Impact

- Frontend-only behavior plus one config registry entry; no API schema or DB
  changes (the data already flowed through `additionalQuotas`).
- Codex/openai accounts are unaffected; the Fable UI renders only for
  Anthropic accounts that report the scoped window, so the stat and meters
  disappear naturally under the Codex provider filter.
