# Design

## Context

See proposal.md for motivation. `limit-watch` is a stdlib-only launchd poller and `seat-guard.py` consumes four existing snapshot keys. The change must remain compatible with Python 3.9 and must not mutate user state while testing.

## Goals / Non-Goals

**Goals:**
- Produce fail-closed Anthropic eligibility from API response fields.
- Preserve the guard's existing snapshot contract while adding operator diagnostics.
- Keep all diagnostics free of emails and full account identifiers.

**Non-Goals:**
- Change seat-guard thresholds or alter live account records.
- Infer missing Anthropic quota data from reset timestamps or unrelated quota rows.

## Decisions

- Centralize eligibility evaluation in a pure helper that returns usability, reason strings, usable percentages, and freshness state. This avoids one rule for counts and another for diagnostics.
- Treat only `int` and `float` (not `bool`) as numeric percentages. This rejects malformed JSON values without coercion.
- Read Fable remaining capacity from `anthropic_fable_scoped_weekly.primaryWindow.usedPercent` and calculate `100 - usedPercent`; missing/non-numeric quota data does not qualify.
- Never use `lastRefreshAt`: it is an auth-token refresh timestamp, not a usage signal. Report ordinary-account freshness as unknown. For Fable-only admission, use `fableScopedWeekly.fresh` and its ISO `recordedAt`; a false flag or timestamp older than fifteen minutes excludes only Fable admission.

## Risks / Trade-offs

- [Additional quota shape changes] → Missing or malformed Fable data excludes only Fable admission and is covered by fixtures.
- [More accounts excluded after deployment] → The snapshot reasons identify the failure class with truncated account IDs. A future `rateLimitResetAt` remains a hard exclusion.

## Migration Plan

Deploy the versioned poller with the existing snapshot keys unchanged. Rollback is a source rollback; no state migration is required because the snapshot is overwritten atomically on the next poll.
