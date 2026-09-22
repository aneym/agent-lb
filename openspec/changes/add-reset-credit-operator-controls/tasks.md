# Tasks

- [x] 1. Settings: `reset_credit_auto_redeem_max_per_day` (default 2); cooldown back to 900 s as minimum spacing
- [x] 2. `ResetCreditAttemptsRepository.count_applied_since` (applied `trigger="auto"` rows only)
- [x] 3. Scheduler consults the rolling-24h allowance before the exhaustion sweep; expiry sweep stays exempt
- [x] 4. Dashboard: reset-credit schemas, API functions, `resetCreditMutation`, "Reset limits (N)" button + confirm dialog, MSW handlers
- [x] 5. Menubar: `redeemResetCredit` client/AppState action, `Account.canRedeemResetCredit`, tappable chip + context-menu item
- [x] 6. Tests: unit (allowance arithmetic, kill switch, sweep suppression), integration (ledger counting, same-day second reset granted), dashboard (button gating), menubar (eligibility + consume-code decoding)
- [x] 7. Deploy to the runtime with verified hashes, rebuild the menubar app, verify live (2026-09-10: sync verified by hash, main service + desktop-proxy kickstarted, CONNECT through :2458 ok, menubar rebuilt and running from launchd, live manual redemption returned `code: reset` and restored the account to `active`)
