## 1. Inventory and integration contract

- [x] Verify the latest stable release and pin its exact commit.
- [x] Compare ancestry and preview the merge without changing runtime code.
- [x] Record migration and conflict inventories with a reproducible command.
- [x] Define fork preservation requirements and ordered implementation slices.

## 2. Persistence and shared infrastructure

- [x] Map upstream migrations against fork schema and private revisions.
- [x] Add forward-only compatibility migrations and converge to one head.
- [x] Prove empty SQLite/PostgreSQL upgrades, existing-fork upgrades, and schema parity.
- [x] Port shared invalidation, leader/claim ownership, logging, and timeout validation.
- [x] Review pool sizes, background-task ownership, and shutdown behavior against fork defaults.

## 3. Accounts, auth, quota, and usage

- [x] Integrate refresh/redemption claims, OAuth persistence, and Auth Guardian behavior.
- [x] Preserve provider boundaries, federation, member/pool attribution, and probe confirmation.
- [x] Integrate usage rollups, retention, pending deletion, and settlement behavior.
- [x] Exercise OpenAI and Anthropic account selection and accounting using disposable fixtures.

## 4. Proxy capabilities and recovery

- [x] Port model sources, embeddings, service tiers, reasoning policies, and model metadata.
- [x] Port durable bridge/file lineage, recovery, and ownership fencing into split fork modules.
- [x] Preserve Claude billing/system blocks, account-bound egress, cost caps, and trace attribution.
- [x] Exercise HTTP/SSE, WebSocket, compact, cancellation, reconnect, and partial-failure paths.

## 5. Dashboard, configuration, and release

- [x] Integrate new backend contracts into the fork's cockpit and preserve its operator flows.
- [x] Review telemetry consent, settings defaults, retention, and environment compatibility.
- [x] Build frontend and package; verify installed CLI, assets, health, and migration entry points.
- [x] Reconcile conflicted upstream surfaces and document intentional fork-specific alternatives.
- [ ] Run strict OpenSpec validation and full exact-SHA local CI on the equipped coordinator.
- [ ] Obtain maintainer review, finish the integration, and then update release metadata.

## Remaining readiness gates

- [ ] Resolve the two policy tests that also fail at the original fork commit.
- [ ] Run the eight hook-dispatcher test files on an isolated equipped runner.
- [ ] Obtain the required cross-vendor custody/spend-cap review.
- [ ] Review the imported file-size overages listed in `size-overages.tsv`.
- [ ] Run `factory-lint`, frontend ESLint, `trivy`, and exact-SHA local CI.
- [ ] Execute disposable-provider journeys when their identity backend exists.
