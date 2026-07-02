## Why

The menubar pool metrics strip showed usage numbers (cost, tokens) with no sense
of what they were worth versus what the pool's subscriptions cost — the "value"
of pooling flat-rate plans instead of paying per-token was invisible. Separately,
the panel has no way to be screenshotted or shared without exposing every
account's real email/display name and the operator's remote hostname, which
blocks casual sharing of an otherwise interesting pool-health view.

## What Changes

- `PlanPricing.swift`: per-(provider, planType) monthly USD list-price table
  (fallback only; operator-entered `subscription.amount` in USD wins) and
  `ArbitrageStats`, a pure pool-global computation of the weekly value multiple
  from `summary.cost.totalUsd7d` over Σ prorated weekly plan cost across
  `isHeadlineCountable` accounts.
- Pool metrics strip gains a value-multiple line (`PoolSection.swift`): bold
  "N×" (monochrome, weight only) + secondary mono "value · $X vs $Y/wk", with
  a `≈` prefix when any counted plan used the fallback table, a `.help()`
  breakdown tooltip, and the line rendering only when a multiple is
  computable. `PanelLayout`'s `metricsLines` input accounts for the extra line
  so panel height stays deterministic.
- `PrivacyMask.swift`: `@AppStorage("privacyMode")`-backed redaction of
  identity-revealing text only — account display names/duplicate tags →
  stable per-provider pseudonyms keyed on sorted `accountId`, remote host chip
  → `"remote"`, pool tooltip account names → pseudonyms. Aggregate numbers
  (percentages, cost, tokens, the value multiple) are never redacted. Exposed
  via `EnvironmentValues.privacyMask`, built once in `RootView` from the full
  account list so pseudonyms stay stable across refresh/sort/scope.
- Header gains an eye/eye.slash glass toggle button and the overflow menu
  gains a "Hide Sensitive Info" toggle, both bound to the same
  `@AppStorage("privacyMode")` key.

## Impact

- Client-only change (`clients/macos-menubar`); no server or API changes.
  Verified with `swift test` including new `PlanPricing`, `PrivacyMask`,
  `Format`, and `PanelLayout` cases.
- User-visible copy is deliberately framed as "value" everywhere — see
  `context.md` for the copy guardrail rationale.
