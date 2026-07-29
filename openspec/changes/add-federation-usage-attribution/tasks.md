# Tasks — add-federation-usage-attribution

- [x] 1. Schema + migration: `federation_usage_daily` table
      (instance_id, account_id, provider, day, requests, input_tokens,
      output_tokens, cache_read_tokens, cost, session_count,
      last_request_at, reported_at; PK instance_id+account_id+day) on the
      current Alembic head, with downgrade.
- [x] 2. Federation schemas: `FederationUsageReportRequest` /
      `FederationUsageDayRollup` / response model.
- [x] 3. Owner side: `POST /api/federation/usage-report` on the existing
      peer-auth router; repository upsert (idempotent, per-instance scoped).
- [x] 4. Follower side: rollup query over local `request_logs`
      (account_id × UTC day, trailing `AGENT_LB_FEDERATION_USAGE_WINDOW_DAYS`,
      default 7); `peer_client.push_usage_report`; scheduler hook after
      successful `mirror_once` (failures logged, isolated, retried next cycle).
- [x] 5. View: `GET /api/usage/instances` (dashboard auth) merging live local
      rollup with stored reports; local id computed live, never duplicated.
- [x] 6. Tests: endpoint auth + idempotent upsert; scheduler push isolation
      (mirror succeeds when push fails); window bounds; instances view merge
      on owner and on empty follower; migration up/down.
