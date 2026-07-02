# Tasks

- [x] `PlanPricing.swift`: monthly USD list-price table (provider × planType)
      and operator-amount-wins-in-USD resolution.
- [x] `ArbitrageStats.compute`: pool-global weekly value multiple from
      `summary.cost.totalUsd7d` over Σ prorated plan cost across
      `isHeadlineCountable` accounts; nil when no value or no priced account.
- [x] `PrivacyMask.swift` + `EnvironmentValues.privacyMask`: pseudonym build
      from the full account list, host redaction, name lookups.
- [x] Header eye/eye.slash glass toggle button + overflow "Hide Sensitive
      Info" toggle, both bound to `@AppStorage("privacyMode")`.
- [x] `PoolSection.swift`: value-multiple line (bold N× + secondary
      breakdown), `.help()` tooltip, pseudonym substitution in the pool
      reset-schedule tooltip.
- [x] `RootView.swift` / `AccountsSection.swift`: `metricsLines` wiring for
      the extra line, `privacyMask` environment injection, account row and
      remote-host redaction.
- [x] Tests: `PlanPricing`, `PrivacyMask` (pseudonym stability, host
      redaction), `Format.multiple`/`Format.usdCompact`, `PanelLayout`
      metrics-line height cases (`swift test`).
- [x] Accessibility labels for the privacy toggle and refresh/overflow
      buttons.
- [x] OpenSpec delta (`macos-menubar-client`).
- [x] Design doc: append §11 (value multiple) and §12 (privacy mode) to
      `macos-menubar-app/design.md`.
- [x] Rebuild and reinstall the local menubar bundle.
