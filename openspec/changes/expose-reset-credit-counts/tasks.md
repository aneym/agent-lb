## 1. Server: cached counts

- [x] 1.1 Add the usage-refresh-policy and frontend-architecture deltas.
- [x] 1.2 Add the reset-credit count cache module and record counts at every upstream listing call site.
- [x] 1.3 Refresh the cached count best-effort after successful redemption (manual and scheduler paths); clear it when the refetch fails.
- [x] 1.4 Expose `resetCreditsAvailable` on `GET /api/accounts` for OpenAI accounts.
- [x] 1.5 Tests: cache record/clear semantics, mapper exposure, post-redeem refresh, non-OpenAI accounts stay null.

## 2. Menubar: per-account chip

- [x] 2.1 Add the macos-menubar delta.
- [x] 2.2 Decode optional `resetCreditsAvailable` and render the chip on OpenAI rows (hidden when unknown, dimmed at zero).
- [x] 2.3 Build the bundle.

## 3. Validation and rollout

- [ ] 3.1 ruff + focused pytest + strict OpenSpec validation.
- [ ] 3.2 Restart the live service, verify `resetCreditsAvailable` appears on `GET /api/accounts` after a sweep, redeploy the menubar bundle, push main, fast-forward the MacBook when reachable.
