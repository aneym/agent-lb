# Tasks

- [x] Add `fresh: bool = False` query param to `GET /api/accounts`; pass
      `include_request_usage` through to the service.
- [x] Gate the request-usage aggregation in `AccountsService.list_accounts` behind
      `include_request_usage`; default returns `requestUsage = null`.
- [x] Short-TTL cache the request-usage aggregation.
- [x] Short-TTL cache the per-account additional-quota windows (the ~14 serial
      round-trips that remain on the default path); clear caches between tests.
- [x] Scope the additional-usage `latest_by_account` queries by `account_ids`.
- [x] Dashboard `listAccounts()` requests `?fresh=1`.
- [x] Existing request-usage tests opt into `?fresh=1`; add a regression test
      asserting the default omits `requestUsage` and `?fresh=1` includes it.
- [x] `cc` launcher caps the `/api/accounts` banner enrichment timeout
      (`CLAUDE_LB_BANNER_TIMEOUT`, default 1.5s).
