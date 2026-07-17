# Tasks

- [x] 1. Upstream client `app/core/clients/rate_limit_resets.py`: payload models, `fetch_reset_credits`, `consume_reset_credit`, direct account-pinned egress (probe precedent), error envelope handling
- [x] 2. `AccountsService.list_rate_limit_reset_credits` / `redeem_rate_limit_reset_credit` with provider/status gating, auth refresh, UUID `redeem_request_id`, post-redeem usage refresh + selection cache invalidation
- [x] 3. Schemas + `GET`/`POST /api/accounts/{account_id}/rate-limit-reset-credits[/consume]` routes with audit logging
- [x] 4. Unit tests (client parsing, service gating) and integration tests (both routes, mocked upstream)
- [ ] 5. `ruff check app clients` + targeted pytest green; live service restarted; end-to-end redemption exercised against a real exhausted account
