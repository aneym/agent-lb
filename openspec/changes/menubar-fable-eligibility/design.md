## Context

The server already computes per-account Fable availability as
`AccountSummary.fableEligible`, using the same routing-aware contract that
excludes paused, disconnected, subscription-canceled, and scoped-exhausted
Claude accounts. The macOS menubar decodes `/api/accounts` but currently drops
that field.

## Goals / Non-Goals

**Goals:**

- Surface the existing Fable availability signal in Claude account rows.
- Keep the Swift client layout fixed-height and glanceable.
- Pin the consumed field in menubar fixtures/tests.

**Non-Goals:**

- Recompute Fable eligibility in the Swift client.
- Add or change backend API fields.
- Add Fable-specific filters or routing controls.

## Decisions

- Use `fableEligible` as the only source of truth. This avoids duplicating the
  server's scoped-weekly, heuristic, subscription, and routability rules in the
  client.
- Render the signal as short title-row text (`Fable` / `Fable out`) next to the
  plan chip. This keeps the weekly quota cell unchanged and preserves the
  fixed 52 pt row.

## Risks / Trade-offs

- A stale account response can briefly show stale Fable availability -> the
  existing polling and per-row refresh controls update the account list.
- The short label is less detailed than showing the scoped percentage -> the
  row remains compact, and the dashboard/API remain the detailed source.
