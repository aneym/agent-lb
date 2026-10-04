# Federation request-log forwarding

## Why

Box edges log Claude and Codex traffic only in their own `request_logs`. Studio's Postgres never sees those rows, so room attribution, the 5h/7d columns, quota audits, and `agent-lb latency` miss box sessions. Daily usage rollups already arrive; this forwards the rows themselves.

## What Changes

- A mirror that has a peer URL and mirror token forwards `request_logs` rows with `id` above a cursor, in id order, batches of at most 500, at most 10 batches per mirror cycle, to `POST /api/federation/request-logs`.
- The cursor lives in `<AGENT_LB_DATA_DIR>/federation-request-log-cursor.json` and is replaced only after a 2xx. The first run, with no cursor, starts at rows from the last 48 hours.
- `AGENT_LB_FEDERATION_FORWARD_REQUEST_LOGS` (`federation_forward_request_logs`, default true) set to false turns the push off. A forward failure does not fail the mirror pull or the usage push.
- Studio stores each row with `source = edge:<instance_id>`, `api_key_id` null, and `account_id` only when that account exists locally. The same `request_id` and `source` is skipped. Deleted edge rows are not sent.
- `list_local_usage_rollups` ignores `source` values that start with `edge:` so Studio's own rollup is not added on top of the stored edge rollups.

## RequestLog readers

Exclude means the query drops `source LIKE 'edge:%'` (null source stays). Include means forwarded rows stay visible.

| Reader | Decision | Reason |
| --- | --- | --- |
| `FederationRepository.list_local_usage_rollups` | exclude | Local per-instance total added to stored edge rollups |
| `AccountsRepository.list_request_usage_summary_by_account` | include | Quota audit of account spend |
| `AccountsRepository` account merge/delete updates of `RequestLog` | include (writers) | Edge copies stay with the account they name |
| `ApiKeysRepository.list_usage_summary_by_key` | exclude | API-key totals and limits |
| `ApiKeysRepository.get_usage_summary_by_key_id` | exclude | API-key totals and limits |
| `ApiKeysRepository.usage_7d_by_account` | exclude | API-key totals and limits |
| `ApiKeysRepository.trends_by_key` | exclude | API-key totals and limits |
| `ApiKeysRepository.usage_7d` | exclude | API-key totals and limits |
| `DashboardRepository.list_logs_since` | include | Request list (delegates to `RequestLogsRepository.list_since`) |
| `DashboardRepository.aggregate_logs_by_bucket` | include | Dashboard trend of stored logs, not the federation rollup sum |
| `DashboardRepository.aggregate_activity_since` | include | Dashboard activity of stored logs |
| `DashboardRepository.top_error_since` | include | Audit of stored errors |
| `QuotaPlannerRepository.warmup_cost_since` | exclude | Warmup budget that feeds routing |
| `QuotaPlannerRepository.aggregate_demand_slots` | exclude | Demand forecast that feeds routing |
| `QuotaPlannerRepository.aggregate_demand_bins` | exclude | Demand forecast that feeds routing |
| `ReceiptsRepository.page` | include | Request list and latency |
| `ReceiptsRepository.aggregate` | include | Audit and latency |
| `ReceiptsRepository` latency percentile | include | Latency |
| `ReportsRepository.aggregate_daily` | include | Audit |
| `ReportsRepository.aggregate_by_model` | include | Audit |
| `ReportsRepository.aggregate_by_account` | include | Audit |
| `ReportsRepository.count_active_accounts` | include | Audit |
| `SessionsRepository.list_aggregates` | include | Session attribution |
| `SessionsRepository.get_aggregate` | include | Session attribution |
| `SessionsRepository.list_models` | include | Session attribution |
| `SessionsRepository.list_sparklines` | include | Session attribution |
| `SessionsRepository.list_series` | include | Session attribution |
| `SessionsRepository.list_seats` | include | Seat attribution |
| `SessionsRepository.list_latency_histogram` | include | Latency |
| `SessionsRepository.list_tokens_per_request_histogram` | include | Audit |
| `SessionsRepository.list_recent_requests` | include | Request list |
| `SessionsRepository.resolve_session_id` | include | Request list lookup |
| `TeamRepository.aggregate_usage` | exclude | Member cap |
| `TeamRepository.aggregate_usage_by_model` | exclude | Member cap |
| `TeamRepository.aggregate_usage_by_day` | exclude | Member cap |
| `team.pool_share._aggregate` | exclude | Pool-share gate on the routing path |
| `usage.builders._cost_summary_from_logs` | include | Renders the rows its caller already selected |
| `usage.builders._usage_metrics` | include | Renders the rows its caller already selected |
| `usage.builders._sum_tokens` | include | Renders the rows its caller already selected |
| `usage.builders._sum_cached_input_tokens` | include | Renders the rows its caller already selected |
| `usage.builders._top_error_code` | include | Renders the rows its caller already selected |
| `RequestLogsRepository.aggregate_usage_window` | include | Quota-audit metrics over stored logs; not added to federation rollups |
| `RequestLogsRepository.aggregate_callers_window` | include | Caller attribution |
| `RequestLogsRepository.list_since` | include | Request list |
| `RequestLogsRepository.list_recent` | include | Request list |
| `RequestLogsRepository.list_filter_options` | include | Request list |
| `RequestLogsRepository.anthropic_cache_tokens_since` | include | Cache audit |
| `RequestLogsRepository.aggregate_by_bucket` | include | Dashboard trend |
| `RequestLogsRepository.aggregate_activity_since` | include | Dashboard activity |
| `RequestLogsRepository.top_error_since` | include | Error audit |
| `RequestLogsRepository.find_latest_account_id_for_response_id` | exclude | Sticky routing lookup |
| `public_usage.build_public_usage` | include | Audit of logs stored on this instance; the federation instances view is the rollup that excludes `edge:` |

## Impact

- Affected specs: `instance-federation`
- Affected code: `app/modules/federation/` (scheduler, peer client, api, schemas, repository, service), settings, and the readers marked exclude above.
- No migration. `source` and `request_id` already exist.
