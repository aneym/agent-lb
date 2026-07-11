## Context

Anthropic message 429s are persisted as append-only additional-usage rows keyed by the request quota family. Selection trusts the latest row until its recorded reset. The account pulse already bypasses selection to probe a single account, but its Fable probe only runs after the account crosses the configured weekly threshold and writes only the Fable-access marker. Consequently, a healthy below-threshold account can remain excluded by an obsolete `anthropic_top` or `anthropic_top_thinking` cooldown even after its upstream entitlement recovers.

## Goals / Non-Goals

**Goals:**

- Re-admit a healthy Fable account without manual database repair when an account-pinned probe decisively contradicts persisted Fable model-quota cooldowns.
- Preserve the append-only usage-history model and existing selection behavior.
- Keep probe failures conservative: only a 2xx can clear cooldown state.

**Non-Goals:**

- Clearing general 5-hour or weekly usage.
- Clearing non-Fable, fast-mode, subscription, authentication, or account-health state.
- Retrying user requests inside the pulse or changing the public probe API.

## Decisions

1. The full account pulse will inspect the latest `anthropic_top` and `anthropic_top_thinking` primary-window rows for each routable Anthropic account. It will run the Fable probe when either row is an active cooldown, even if weekly usage is below the Fable threshold. This reuses the established leader-elected, bounded-concurrency recovery mechanism instead of adding request-path latency.

2. Reconciliation will probe each active cooldown with its exact quota shape: `claude-fable-5` without thinking for `anthropic_top`, and `claude-fable-5` with adaptive thinking for `anthropic_top_thinking`. A success can therefore contradict only the quota family it actually exercises.

3. A 2xx result will append a zero-percent row only for the probed active Fable model-quota cooldown key. A 429, refusal, server error, or transport failure will not clear that key. The existing Fable-access marker remains governed by the weekly-threshold logic.

4. Cooldown lookup and append-only clearing will be injected into `AccountPulseScheduler`, matching its existing usage lookup and marker writer seams. Each lookup returns the observed latest row id. Clearing is a compare-and-append operation that writes zero only if that row is still latest, preventing a successful probe from overwriting a newer concurrent 429. Production implementations use short-lived background sessions; unit tests use deterministic fakes.

Alternatives considered:

- Ignoring model cooldowns whenever general usage says 0% was rejected because general usage does not prove model-specific capacity.
- Probing synchronously whenever selection finds no account was rejected because it adds network work and thundering-herd risk to the request path.
- Deleting old history was rejected because usage history is append-only evidence and deletion would damage auditability.

## Risks / Trade-offs

- [A real cooldown receives one extra tiny probe per quota key per full pulse] -> Probe only accounts with active relevant cooldown rows, retain leader election and bounded concurrency, and leave each cooldown untouched on any non-2xx.
- [Adaptive-thinking probe contract changes upstream] -> Keep the request shape shared and regression-tested; failures are inconclusive and cannot unlock routing.
- [A newer 429 lands while a reconciliation probe is in flight] -> Compare the observed cooldown row id with the latest row atomically before appending cleared state; mismatch is a no-op.
- [Recovery waits until the next full pulse] -> This is bounded by the configured account-pulse interval and avoids adding latency to live user requests.

## Migration Plan

No schema migration is required. Deploy the server change, restart the local service, and verify that a seeded stale cooldown is cleared by a successful pulse probe and that a real high-effort `cc` request routes successfully. Rollback consists of reverting the code; appended zero rows remain valid historical evidence of successful probes.

## Open Questions

None.
