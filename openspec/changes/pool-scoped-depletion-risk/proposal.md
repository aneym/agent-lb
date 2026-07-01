## Why

`/api/dashboard/projections` reported the pool's depletion risk as the single worst
account's risk, aggregated across all providers, with no attribution. Observed in
production: one Anthropic account absorbing a heavy interactive session burned its
weekly window at ~5.8%/hour, which flipped `depletionSecondary.riskLevel` to
`critical` while the pool as a whole had ~81% of its weekly credits remaining and
the OpenAI accounts were burning at ~0.6%/hour. Dashboards rendered "risk:
critical" against a visibly healthy pool, and scoped views (e.g. Codex) inherited
risk driven entirely by a different provider's account. The balancer fails over
between accounts, so a single hot account does not exhaust the pool.

## What Changes

- `compute_aggregate_depletion` now returns **pool-level** risk: the mean of
  per-account risks (equal weights), classified with the same thresholds. The
  worst account is surfaced via new attribution fields (`worst_account_id`,
  `worst_risk`, `worst_risk_level`, `account_count`).
- Exhaustion ETA fields (`projected_exhaustion_at`, `seconds_until_exhaustion`)
  carry the worst account's projection only while the pool itself is
  danger/critical, so a calm pool never renders an alarming exhaustion date.
- `DepletionResponse` gains optional `worstAccountId`, `worstAccountEmail`,
  `worstRisk`, `worstRiskLevel`, `accountCount` (backward-compatible additions).
- `DashboardProjectionsResponse` gains `depletionPrimaryByProvider` /
  `depletionSecondaryByProvider` maps (provider → DepletionResponse) so scoped
  UIs can show their own provider's risk instead of the global one.

## Impact

- Existing dashboards immediately show pool-level risk without a frontend
  rebuild (the top-level field semantics change); the worst-account signal
  remains available for surfaces that want per-account alarms.
- Per-account depletion math (EWMA) is unchanged; only aggregation and the
  response contract changed. Regression test at the projections route: one hot
  account among five calm ones keeps the pool out of danger/critical, carries
  attribution, and splits by provider.
