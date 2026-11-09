# Usage export

A timer on the agent-lb primary writes two JSON files every 15 minutes so dashboards and scripts can read usage and headroom without a model turn or a database login. It only reads: one repeatable-read, read-only transaction over `request_logs`, `accounts`, `usage_history` and `additional_usage_history`.

| File | What it holds |
| --- | --- |
| `usage-48h.json` | `buckets`: one row per UTC hour, provider, model, `client_session_id` and account (first 8 characters of the id) for the last 48 hours, with `requests`, `ok_requests`, `failed_requests`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cache_write_5m_tokens`, `cache_write_1h_tokens`, `cost_usd` (stored) and `rows_without_cost`. `by_model` and `totals` cover the 48 hours; `by_model_24h` and `totals_24h` cover exactly the 24 hours up to `window.end_utc`. |
| `headroom.json` | `accounts_now`: per Anthropic account `status`, the weekly all-models percent and reset, the Fable weekly bucket percent and reset, `resets_held` (banked reset credits, `resetCreditsAvailable` from the local `/api/accounts`, the count `agent-lb resets` shows; null when agent-lb holds no current count for the account; `resets_held_source` says where it came from this run), the 5-hour window, `limits_recorded_at`, `usable_now` and a `note` with every reason behind it. `usable_now` follows the router's own rule from stored state, spelled out in `usable_rule`: a missing 5-hour reading counts as 0%, as it does in the balancer. Per account also `weekly_all_points_left`, `all_models_points_moved_6h`, `cost_usd_6h` and `usd_per_all_models_point` (null under 2 points moved). Top level: `fable_usd_per_hour` (stored Fable 5.2 cost per hour over 1 and 6 hours), `usd_per_all_models_point` (pooled, 6 hours), `pool_hours_left_last_hour_rate` and `pool_hours_left_24h_rate` (points left on accounts with room times dollars per point, over the last 60 minutes' or the 24-hour average stored cost per hour), `next_weekly_reset`, `next_weekly_reset_hours`, `pool_hours_left_basis` and `pool_summary`. |

Both carry `generated_at`, `window` and `source`. Each is written to a temp file and renamed, and pushed with rsync, which also renames into place, so a reader never sees half a file. A failed run exits 1, writes nothing new and leaves the last good files; the next tick tries again.

## Not in the file

- Plan and rate-limit tier: agent-lb stores `plan_type` as `claude` for every Anthropic account and does not store the tier, so neither is exported.
- A Fable-only count of held resets: agent-lb keeps one banked reset count per account, not one per limit, so the file carries `resets_held` instead.
- Readings come from the latest row in the last 24 hours; a `note` names a weekly reading older than 30 minutes.
- Some accounts get no 5-hour window from Anthropic: neither the usage poll nor the response headers carry `5h` values, only `7d`. Their `five_hour_*` fields are null and the `note` says so.

## Low headroom alert

When `pool_hours_left_last_hour_rate` first falls below 3 hours and the next weekly reset is further away than that, the run sends one alert to `factory` through `orch-watch-alert --message`, the route coding-agents-sync's drift alert uses. It fires again only after the figure has gone back above 6 hours and fallen again; the armed state lives in `~/.agent-lb/usage-export-alert.json` on the job host. A failed send exits 1 after the files are written, and the next tick tries again.

## Where it runs

The primary is ax42 since 2026-10-27. The job runs there from the followed runtime (`~/.agent-lb-primary/runtime/agent-lb`) and pushes both files to `ax102:/home/jobs/.agent-rails/factory/agent-lb-export/`. On the target host itself it writes there directly.

```sh
~/.agent-lb-primary/runtime/agent-lb/scripts/install-usage-export.sh
systemctl --user list-timers agent-lb-usage-export.timer
tail ~/.agent-lb/usage-export.log
```

Each log line names the bucket count, file sizes, wall time and CPU time of the run (about 1 s wall and 0.3 s CPU on 2026-11-09).
