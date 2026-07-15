## Why

`AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE=true` is intended to let the Claude pool use paid extra-usage capacity only after every subscription-covered account has exhausted its five-hour window. Today, the first credit-billing response records a cooldown that excludes the same credit-backed account before the last-resort fallback runs, so the opt-in silently stops working while paid capacity remains available.

## What Changes

- Make Anthropic extra-usage opt-in override only the primary-window exhaustion cooldown when all subscription-covered candidates are unavailable.
- Keep subscription-covered accounts preferred whenever any remain eligible.
- Require the exact usable-credit invariant `credits_unlimited is true OR (credits_has is true AND credits_balance > 0)` before a cooled account can be used as the paid fallback.
- Store successful credit-billing tripwires under a dedicated quota key so they remain distinguishable from authoritative request-quota `429` cooldowns.
- For Fable requests, determine model scope before primary-window exhaustion: fresh Fable-scoped exhaustion with a future reset is a hard exclusion, while elapsed or unknown resets stay in scope and weekly heuristics and capable markers only prefer candidates within the remaining scope.
- Pass only the exact paid-fallback account IDs into a request-scoped primary-quota bypass; do not bypass secondary or weekly exhaustion, and do not clear or weaken persisted status for unrelated requests.
- Preserve non-primary quota-key cooldowns, non-quota account-state gates, model eligibility, and all other routing safeguards.
- Add regression coverage for paid fallback, subscription-first preference, disabled opt-in, and non-credit cooldown preservation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Clarify that the explicit Anthropic extra-usage opt-in re-admits a credit-backed account at pool exhaustion even when the credit-billing tripwire recorded the primary-window cooldown.

## Impact

- Affected code: Anthropic account eligibility and cooldown filtering in `app/modules/proxy/anthropic_service.py`.
- Affected tests: Anthropic proxy integration coverage around extra-usage selection and cooldowns.
- Operational impact: when the existing opt-in is true, Claude traffic may incur paid Anthropic extra-usage charges after all subscription-covered five-hour capacity is exhausted.
