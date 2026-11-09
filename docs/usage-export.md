# Usage export

A timer on the agent-lb primary writes two JSON files every 15 minutes so dashboards and scripts can read usage and headroom without a model turn or a database login. It only reads: one repeatable-read, read-only transaction over `request_logs`, `accounts`, `usage_history` and `additional_usage_history`.

| File | What it holds |
| --- | --- |
| `usage-48h.json` | `buckets`: one row per UTC hour, provider, model, `client_session_id` and account (first 8 characters of the id) for the last 48 hours, with `requests`, `ok_requests`, `failed_requests`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cache_write_5m_tokens`, `cache_write_1h_tokens`, `cost_usd` (stored) and `rows_without_cost`. `by_model` and `totals` cover the 48 hours; `by_model_24h` and `totals_24h` cover exactly the 24 hours up to `window.end_utc`. |
| `headroom.json` | `accounts_now`: per Anthropic account the weekly all-models percent and reset, the Fable weekly bucket percent and reset, `fable_resets_held` (not read yet, always null), the 5-hour window and `usable_now`. `fable_usd_per_hour` gives the pool's stored Fable 5.2 cost per hour over the last 1 and 6 hours. `pool_summary` lists usable accounts. |

Both carry `generated_at`, `window` and `source`. Each is written to a temp file and renamed, and pushed with rsync, which also renames into place, so a reader never sees half a file. A failed run exits 1, writes nothing new and leaves the last good files; the next tick tries again.

## Where it runs

The primary is ax42 since 2026-10-27. The job runs there from the followed runtime (`~/.agent-lb-primary/runtime/agent-lb`) and pushes both files to `ax102:/home/jobs/.agent-rails/factory/agent-lb-export/`. On the target host itself it writes there directly.

```sh
~/.agent-lb-primary/runtime/agent-lb/scripts/install-usage-export.sh
systemctl --user list-timers agent-lb-usage-export.timer
tail ~/.agent-lb/usage-export.log
```

Each log line names the bucket count, file sizes, wall time and CPU time of the run (about 1 s wall and 0.3 s CPU on 2026-11-09).
