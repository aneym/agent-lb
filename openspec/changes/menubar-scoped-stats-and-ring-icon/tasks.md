# Tasks

- [x] Server: `/api/usage/summary` accepts optional `?provider=<name>`,
      filters windows/cost/metrics to that provider's subscription-usable
      accounts, excludes unattributed (`account_id IS NULL`) request logs
      when scoped, and returns a valid empty summary for an unknown
      provider. No-param path unchanged.
- [x] `APIModels.swift` / summary-fetch call site: pass the active
      `ProviderScope.providerParam` on every `/api/usage/summary` fetch.
- [x] `AppState.statusIconPercents(from:)`: derive icon percents from the
      scoped summary (already scope-driven at the call site; verify no
      pool-global fallback leaks through).
- [x] Stale-fetch guard: a summary request issued under scope A that
      resolves after the scope has changed to B is discarded, never applied
      to state.
- [x] `PoolSection.swift` / value-multiple line: numerator
      (`summary.cost.totalUsd7d`) and denominator (headline-countable
      accounts) both scoped to the active provider; remove the `· all
      providers` pool-global framing this line previously required.
- [x] `StatusIconRenderer.icon(for:primaryPercent:longWindowPercent:)`:
      two-ring rendering (outer = primary/5h, inner = secondary/weekly);
      unknown → track-only both rings; risk → outer ring + exclamation,
      inner omitted; down/update unchanged.
- [x] `AppState.statusIconPercents(from:)` signature update to return both
      `(primary: Double?, longWindow: Double?)` for the renderer.
- [x] Header eye/refresh/overflow buttons and footer power button: remove
      circle glass chip background; keep 22×22 hit target and existing
      accessibility labels. Text buttons unaffected.
- [x] Tests: `ModelDecodingTests` for `?provider=` round-trip fixtures,
      `StatusIconRenderer` two-ring cases (unknown/risk/down/update/normal),
      stale-fetch-guard unit test, scoped value-multiple math case.
- [x] OpenSpec delta: `macos-menubar-client` (scope, icon, chip) +
      server-owning capability for `?provider=` (see specs/).
- [x] Design doc: append §13 to `macos-menubar-app/design.md`; amend §11's
      "Pool-global by construction" bullet to mark it superseded by §13.
- [x] Rebuild and reinstall the local menubar bundle; restart
      `com.aneyman.agent-lb` and exercise `/api/usage/summary?provider=`
      against the live service.
