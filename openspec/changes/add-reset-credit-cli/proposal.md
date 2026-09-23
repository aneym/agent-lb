## Why

Operators need a deliberate command-line path to inspect banked reset credits and attempt a single redemption without confusing inventory with provider eligibility.

## What Changes

- Add `agent-lb resets list --account <email-or-id>` and `agent-lb resets redeem --account <email-or-id> [--credit-id ID] [--yes]`.
- Resolve an exact, unique account through `GET /api/accounts`; list through the live account credit endpoint; make redemption a confirmed, single POST.
- Show banked inventory separately from current provider eligibility, and report no-op redemption codes as nonzero results.
- Honor a Claude grant's `use_requires_limit: false` signal when the provider marks that grant usable now, while retaining exhausted-limit checks for ordinary grants.

## Impact

- Capability: `reset-credit-cli`.
- Adds optional eligibility fields to the existing inventory response and corrects Claude grant eligibility; automatic redemption policy is unchanged.
