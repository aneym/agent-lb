# Add No-Accounts Selection Diagnostics

## Summary

"No available accounts" failures previously returned an opaque 503 with a one-line message. Operators and clients could not tell which accounts were excluded, why, or when the pool recovers — so clients blind-retried into an exhausted pool and a lapsed subscription could hide behind discarded usage refreshes for days.

## Problem

Three gaps compounded into undiagnosable outages:

- Selection failures discarded the per-account exclusion buckets the balancer already computed, so the error envelope could not say who was rate-limited, cooling down, paused, or deactivated, nor when the earliest account recovers.
- Clients had no machine-readable recovery hint (`Retry-After` / reset seconds) on no-accounts 429/503 responses, so they retried immediately and repeatedly.
- The usage updater correctly refused refresh payloads whose identity (plan/workspace) contradicted the stored slot, but only warned once per cycle; a chronically mismatched account (e.g. lapsed Pro plan) stayed invisible on the dashboard while routing continued against stale identity.

## Proposed Change

- Thread per-account exclusion detail (status, reset time, cooldown, deactivation reason, plan) and the earliest future recovery time from core balancer selection failures through `AccountSelection` to all no-accounts error surfaces.
- Enrich no-accounts OpenAI-compatible error envelopes (HTTP bridge, proxy service, streaming SSE) with `resets_at`, `resets_in_seconds`, and an `error.diagnostics` object (`degraded`, `accounts[]`, `earliest_recovery_at`, `requested_model`). Derive a standard `Retry-After` header on 429/503 responses from `resets_in_seconds`.
- Escalate chronic usage-refresh identity mismatches: after 3 consecutive discarded refresh cycles for an account, log a single ERROR and expose `identityMismatch` on the accounts API until the next accepted refresh clears it.
- Add `GET /api/availability`: per-provider totals, unavailable accounts with reset times, earliest recovery, and degradation state, behind the dashboard session guard.
