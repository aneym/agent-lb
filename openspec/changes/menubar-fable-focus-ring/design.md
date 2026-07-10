## Context

Fable eligibility is gated per account by a dedicated Fable-scoped weekly
limit that the server ingests as additional usage
`anthropic_fable_scoped_weekly` (change `fable-scoped-weekly-limit`). The
menubar already decodes it per account (`fableRemainingPercent`, change
`surface-fable-remaining`) but has no pool-level, glanceable surface.

## Goals / Non-Goals

**Goals:**

- Show Fable runway in the menu bar exactly when it is relevant — while a
  Claude app has focus — without widening the icon the rest of the time.
- Reuse the existing account polling; no new endpoints or fetch cadence.

**Non-Goals:**

- A second NSStatusItem / MenuBarExtra (macOS 26 registration is fragile; the
  widened single icon avoids new window machinery).
- Reset countdown in the bar; the panel and dashboard stay the detailed view.
- Detecting terminal-hosted Claude Code sessions (no reliable signal from the
  hosting terminal's bundle id).

## Decisions

- Focus signal: `NSWorkspace.didActivateApplicationNotification` plus an
  initial `frontmostApplication` read; match = bundle id prefix
  `com.anthropic.` (covers Claude desktop today and future Anthropic apps).
  No TCC prompt is involved.
- Pool metric: unweighted mean of scoped remaining % across routable
  Anthropic accounts with scoped data. Exhausted-but-routable accounts stay
  in the denominator so the number reflects total pool runway until their
  resets; paused/disconnected/canceled accounts are excluded because their
  capacity is not routable. Scoped capacities are not exposed per account, so
  a credit-weighted mean is not currently possible.
- Rendering: the icon image widens 18 → 38 pt (18 pt cell + 2 pt gap +
  18 pt cell); the Fable cell is one ring at the outer radius using the same
  4 %-bucket cache plus a centered bold 8 pt `F`, dimmed with the same alpha
  as the other rings when the service is down.

## Risks / Trade-offs

- Accounts refresh every 120 s while the panel is closed, so the ring can lag
  a burn by up to two minutes -> acceptable for a glance gauge.
- On notched laptops the widened icon consumes 20 more points of bar space ->
  the existing `NSStatusItem Preferred Position` mitigation on the MacBook
  already accounts for overflow; re-check after rollout.
- The unweighted mean treats a 5x and a 20x plan alike -> revisit if the
  server ever exposes scoped capacity credits.
