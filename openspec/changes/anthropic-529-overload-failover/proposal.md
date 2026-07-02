# Bounded 529/overloaded cross-account failover on the Anthropic messages path

## Why

During the 2026-07-02 00:37–00:57Z partial brownout, 158/500 logged requests
failed `upstream_529 "Overloaded"` (139 on claude-opus-4-8, 14 on
claude-fable-5) spread across ALL four active orgs — while ~290 requests
SUCCEEDED in the same window, i.e. an immediate retry had roughly 60–70% odds.
Fresh `upstream_529` failures are still landing (2 at 05:20Z). The Anthropic
messages path fails over across accounts on 401/403/429 but raises on every
other >=400, so each 529 propagated straight to clients; concurrent
orchestrators exhausted their own retry budgets and their subagents died.

529 is a transient capacity signal, not an account-health or quota signal:
another account (different org allocation) frequently serves the same request
immediately.

## What Changes

- In the Anthropic messages wake loop, an upstream 529 no longer raises:
  the failed account is excluded and the request retries on the next selected
  account within the existing `_MAX_SELECTION_ATTEMPTS` budget, after a short
  jittered backoff (exponential from 0.25s, capped at 2s, injectable for
  tests).
- A 529 records NO quota cooldown (it is not quota) and touches error-health
  only through the existing `record_error`, exactly as today.
- Every 529 attempt still persists its `upstream_529` request-log row.
- When the attempt budget exhausts on 529s, the terminal error surfaces the
  Anthropic-native `overloaded_error` type with status 529 so clients apply
  their own overload retry policy; a true full outage therefore still fails
  fast (bounded attempts, no holding — the pool-exhausted wait only engages
  when a known reset exists).

## Impact

- Client-visible 529s drop to roughly the probability that ALL tried accounts
  are browned out simultaneously, instead of any single account.
- Affected: `app/modules/proxy/anthropic_service.py` (wake-loop 529 branch +
  backoff constants), `tests/integration/test_anthropic_proxy.py`.
