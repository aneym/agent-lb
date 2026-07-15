## Context

Anthropic extra usage keeps returning successful responses after a subscription window exhausts, so agent-lb records a primary-window cooldown as soon as response headers show credit billing. That tripwire protects against accidental spend. The explicit `AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE` opt-in is supposed to turn exhausted credit-backed accounts into last-resort candidates, but the cooldown filter currently removes them before the fallback can re-admit them. The tripwire previously used the request quota key, making it indistinguishable from a real upstream `429` that must remain authoritative.

## Goals / Non-Goals

**Goals:**

- Preserve subscription-covered routing as the first choice.
- When the opt-in is true and no subscription-covered candidate remains in the requested model scope, re-admit only accounts with usable extra-usage capacity whose blocking signal is the exhausted primary window.
- Keep non-quota account state, Fable hard eligibility, model-plan, non-primary quota-key cooldown, and other safety gates intact.

**Non-Goals:**

- Enabling extra usage for accounts or changing billing limits.
- Treating credits as a general override for authentication, subscription, weekly/model, overload, or circuit-breaker failures.
- Changing behavior when the opt-in is false.

## Decisions

1. Record successful credit-billing response tripwires under the dedicated `anthropic_extra_usage` quota key. Real upstream `429` responses continue to write the requested model/quota key, so the two signals cannot be confused during preselection.
2. Re-admit a cooled account only in the existing pool-exhausted last-resort branch and only when its latest usage snapshot satisfies `credits_unlimited is true OR (credits_has is true AND credits_balance > 0)`. Enabled finite credits with a zero or unknown balance are depleted or unproven and remain blocked.
3. Keep requested-quota cooldowns authoritative. A Fable-specific, thinking-specific, fast-mode, overload, or other upstream rejection is not proof that paid extra usage can serve the request.
4. Prove behavior at the Anthropic proxy integration surface with multiple-account fixtures, because helper-only coverage would not establish selection ordering or cooldown interaction.
5. For Fable, classify model scope before applying primary-window gates. A fresh Fable-scoped marker at or above its threshold is a hard scope exclusion only while its reset is known and still in the future. An elapsed or unknown reset remains in model scope so a stale exhaustion value cannot hide subscription-covered headroom and trigger paid spend. Every remaining account is model-scoped; fresh scoped headroom, overall-weekly headroom, and fresh capable probes are preferences within that scope, while heuristic-only over-threshold accounts remain soft fallbacks. Remove active secondary-exhausted accounts from standard eligibility before applying those preferences, so an unusable preferred candidate cannot discard usable soft headroom. If every account is actively hard-excluded, retain those exclusions in terminal quota diagnostics and report the earliest known reset.
6. Authorize paid fallback only when no subscription-covered model-scoped candidate remains and every model-scoped account has an active primary exhaustion. Reapply preferred-over-soft ordering to paid candidates so the heuristic affects which paid account is chosen without stranding healthy subscription headroom.
7. Carry exact paid-fallback IDs into the load balancer as a request-scoped primary-quota bypass. This permits only a persisted `rate_limited` status attributable to active primary-window exhaustion to be ignored for that request without clearing stored status or changing other requests. A secondary or weekly `quota_exceeded` state remains blocking.
8. Compute retry diagnostics from effective per-account availability. When both primary and secondary windows are active, use their later known reset for that account, then report the earliest bounded reset across blocked accounts. If an active secondary exhaustion has no reset, do not advertise the earlier primary reset for that account.

## Risks / Trade-offs

- [Paid traffic can continue until the configured Anthropic limit is reached] -> Require the existing explicit opt-in plus live credit metadata and expose the remaining balance in account surfaces.
- [A stale positive credit snapshot could admit an account after credits are depleted] -> Preserve upstream error handling and cooldown recording; the next response/usage refresh corrects availability.
- [Over-broad cooldown bypass could retry an account that cannot serve the requested model] -> Give the credit-billing tripwire its own quota key, limit the override to that marker plus primary exhaustion, and retain requested-quota cooldown gates.
- [A global quota bypass could leak paid routing into unrelated requests] -> Scope the bypass to exact account IDs produced by this request's paid-fallback eligibility and leave persisted status unchanged.
