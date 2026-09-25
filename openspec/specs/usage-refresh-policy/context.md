# Usage Refresh Policy Context

## Anthropic five-hour window priming

Anthropic priming reuses the limit-warmup sender and durable attempt table, but
does not use the optional OpenAI warmup switch or working-hours planner. After
a five-hour reset, Anthropic's usage endpoint reports the primary window with
0% used and no reset time until the next request opens it; that closed state
can last for hours on an idle account. The scheduler primes from that state,
not from seeing the reset happen: when fresh primary and weekly usage (at most
ten minutes old) show the window closed and weekly capacity free, it sends a
one-token Haiku Messages request. A Haiku-standard sample blocks priming only
when it shows that quota exhausted; idle accounts usually have none.

The attempt is keyed by account and the start of the fixed five-hour bucket
(from the Unix epoch) holding the decision time. Anthropic starts a window at
the request time floored to ten minutes, so a window a primer opens resets in
a later bucket and each closed period gets one primer. A primer that was sent
but did not visibly open a window blocks another for 4h45m, which covers a
bucket boundary right after a send. Explicit failures back off, at most five
retries per bucket; an uncertain in-flight or successful-but-unconfirmed send is
retained without an automatic duplicate. A usage read showing a primary reset
four to six hours after the send marks the attempt confirmed. `agent-lb status`
shows the last confirmed prime time, or `never` when none exists.

The weekly window is not primed. Its reset stays on a fixed per-account weekly
schedule (for example every Friday 18:00 UTC for one account since July) and
rolls over at that time with or without traffic, so a request cannot move it.

For example, if an account's primary window reset at 20:00 UTC and nobody used
it, a usage sample at 20:00:10 showing 0% and no reset time permits one Haiku
primer, and a sample at 23:00 in the same state does not. A later sample with a
reset near 01:00 UTC confirms the new window. A weekly-capped account is skipped.

## Purpose

This context explains how agent-lb derives an account's usage and status, and
how to diagnose disagreements between agent-lb and Codex Desktop or the Codex
CLI quota pill.

agent-lb treats `/wham/usage` as the source of truth for account usage. Other
OpenAI account surfaces can display reset state earlier than `/wham/usage`,
especially during team reset windows, so the dashboard can temporarily show an
account as `rate_limited` even when Codex Desktop says the quota has reset.

## Upstream Usage Source

agent-lb refreshes account usage by calling:

```http
GET https://chatgpt.com/backend-api/wham/usage
```

The call is made per account on the configured refresh tick, which defaults to
60 seconds. The client lives in
[`app/core/clients/usage.py`](../../../app/core/clients/usage.py), and the
scheduler lives in
[`app/core/usage/refresh_scheduler.py`](../../../app/core/usage/refresh_scheduler.py).

## Status Derivation

The fetched usage is fed through
[`apply_usage_quota`](../../../app/core/usage/quota.py), which derives account
status from `primary_window.used_percent`:

- `secondary_used >= 100`, regardless of `primary_used`: `QUOTA_EXCEEDED`
- `used_percent >= 100` on the primary rate-limit window: `RATE_LIMITED`
- `used_percent < 100`: `ACTIVE`

There is no manual reset step inside agent-lb. Recovery is driven by the next
refresh tick that observes a sub-100 value from `/wham/usage`.

## Why Codex Settings Can Disagree

Codex Desktop's Settings -> Account view and `/wham/usage` are fed by different
OpenAI-side data sources:

- `/wham/usage` exposes the rate limiter's internal counter. It updates lazily,
  typically on the next chargeable request through that account, or when its
  internal window crosses `reset_at`.
- Settings -> Account is fed by a separate account/quota view that often picks
  up team-side reset events earlier.

During a reset window it is normal for Settings -> Account to show the reset
state while `/wham/usage` still returns `used_percent: 100` for a short period
afterwards. agent-lb mirrors `/wham/usage` during that window, so the account
stays `RATE_LIMITED` or `QUOTA_EXCEEDED` until upstream catches up.

## Operational Notes

- Wait first. The next request through that account usually wakes the upstream
  rate limiter; agent-lb auto-recovers on the next refresh tick after the
  upstream payload changes.
- A force-probe action is planned in
  [#677](https://github.com/aneym/agent-lb/issues/677). The dashboard should
  expose a per-account button that fires one minimal `responses.create` against
  the affected account to nudge the upstream limiter to re-evaluate the window.
- Do not manually flip the agent-lb account state to `ACTIVE` while
  `/wham/usage` still reports the account as fully used. That only masks the
  upstream state and can route traffic back to an account that the upstream
  limiter will reject.

## Verification Example

To confirm that the disagreement is upstream rather than agent-lb's mirror,
call `/wham/usage` directly with the same account token agent-lb is using:

```bash
ACCESS_TOKEN=...
ACCOUNT_ID=...   # chatgpt-account-id UUID, not agent-lb's id

curl -s https://chatgpt.com/backend-api/wham/usage \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "chatgpt-account-id: ${ACCOUNT_ID}" \
  -H "Accept: application/json" | jq '.rate_limit'
```

If `primary_window.used_percent` is still `100` here while Settings -> Account
shows the account as reset, agent-lb has nothing fresher to mirror. The account
is inside the upstream propagation window, and the practical fix is to wait or,
once #677 lands, use the Probe action.

## Related Work

- [#676 - initial bug report on `/wham/usage` vs. Settings UI divergence](https://github.com/aneym/agent-lb/issues/676)
- [#677 - dashboard per-account force-probe action](https://github.com/aneym/agent-lb/issues/677)
