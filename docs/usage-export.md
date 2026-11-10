# Usage export

A timer on the agent-lb primary writes two JSON files every 15 minutes so dashboards and scripts can read usage and headroom without a model turn or a database login. It only reads: one repeatable-read, read-only transaction over `request_logs`, `accounts`, `usage_history` and `additional_usage_history`.

| File | What it holds |
| --- | --- |
| `usage-48h.json` | `buckets`: one row per UTC hour, provider, model, `client_session_id` and account (first 8 characters of the id) for the last 48 hours, with `requests`, `ok_requests`, `failed_requests`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cache_write_5m_tokens`, `cache_write_1h_tokens`, `cost_usd` (stored) and `rows_without_cost`. `by_model` and `totals` cover the 48 hours; `by_model_24h` and `totals_24h` cover exactly the 24 hours up to `window.end_utc`. |
| `headroom.json` | `accounts_now`: per Anthropic account `status`, the weekly all-models percent and reset, the Fable weekly bucket percent and reset, `resets_held` (banked reset credits, `resetCreditsAvailable` from the local `/api/accounts`, the count `agent-lb resets` shows; null when agent-lb holds no current count for the account; `resets_held_source` says where it came from this run), the 5-hour window, `limits_recorded_at`, `usable_now` and a `note` with every reason behind it. `usable_now` follows the router's own rule from stored state, spelled out in `usable_rule`: a missing 5-hour reading counts as 0%, as it does in the balancer. Per account also `weekly_all_points_left`, `all_models_points_moved_6h`, `cost_usd_6h` and `usd_per_all_models_point` (null under 2 points moved). Top level: `fable_usd_per_hour` (stored Fable 5.2 cost per hour over 1 and 6 hours), `usd_per_all_models_point` (pooled, 6 hours), `pool_hours_left_last_hour_rate` and `pool_hours_left_24h_rate` (points left on accounts with room times dollars per point, over the last 60 minutes' or the 24-hour average stored cost per hour), `next_weekly_reset`, `next_weekly_reset_hours`, `pool_hours_left_basis`, `pool_summary` and `points_by_owner` (see [Ours and the teammate's](#ours-and-the-teammates)). Quota pace in points (`weekly_all_points_last_1h` and the rest, `points_pace`, `pool_hours_left_points_*`): see [Quota pace in points](#quota-pace-in-points). `schema` is `agent-lb-headroom/2` since the points fields. |

Both carry `generated_at`, `window` and `source`. Each is written to a temp file and renamed, and pushed with rsync, which also renames into place, so a reader never sees half a file. A failed run exits 1, writes nothing new and leaves the last good files; the next tick tries again.

## Quota pace in points

What counts is share of weekly quota, not dollars, so `headroom.json` also gives pace in percentage points of the weekly limit, read from the limit readings agent-lb stores (`usage_history` window `secondary` for all models, `additional_usage_history` `anthropic_fable_scoped_weekly` for Fable), never converted from cost.

- Per account in `accounts_now`: `weekly_all_points_last_1h`, `weekly_all_points_last_24h`, `fable_points_last_1h` and `fable_points_last_24h`: points of that weekly limit used in the last 1 or 24 hours.
- `points_coverage` beside them, one entry per field: `from` and `to` (the readings the figure runs between), `readings` and `resets` (weekly resets inside the span), or `missing` with the reason when the field is null.
- A figure runs from the last reading at or before the window start to the latest reading. It is null, never 0 or an estimate, when that first reading is more than 10 minutes before the window start, the latest is more than 10 minutes old, or two readings in the span are more than 60 minutes apart.
- A weekly reset inside the span (the reset time moves forward by more than an hour, or the percent drops by more than 5 points) is not negative use: the figure is use before the reset plus use after it from 0. Readings are whole percents that flicker by a point, so a figure is good to about 1 point; small drops are not resets.
- Top level `points_pace`: the same four names for the pool, each the sum of the account figures that are not null, over the same Anthropic accounts `pool_hours_left_basis.points_left` covers. `accounts` and `accounts_counted` say how many went into each sum, `accounts_without_coverage` lists the ones left out (so with an account missing, the pool figure is a floor on the pace). Also `points_left` (equal to `pool_hours_left_basis.points_left`), `weekly_all_points_per_hour_last_1h` and `weekly_all_points_per_hour_24h`. `by_owner_last_24h` splits the 24-hour all-models and Fable points by `owner`: `primary` for accounts this agent-lb owns, `teammate-owned` for accounts a teammate's agent-lb pushes here (each account entry carries `owner` and `owner_instance`; `pool_summary.teammate_owned` lists them). A redeemed reset's drop is never counted as use: after a reset the span counts from the first reading after it. The points moved behind `usd_per_all_models_point` follow the same rule.
- `pool_hours_left_points_last_hour_rate` and `pool_hours_left_points_24h_rate`: `points_left` over all-models points per hour, last hour or 24-hour average; null when no points moved. They sit beside the dollar-based `pool_hours_left_last_hour_rate` and `pool_hours_left_24h_rate`, which stay for their readers; the low headroom alert reads `points_by_owner` instead.

## Ours and the teammate's

`points_by_owner` splits points left and hours left by who owns the account: `ours` is every account with `owner` `primary`, `teammate` every account with `owner` `teammate-owned` (shared accounts a teammate's agent-lb pushes here, where moves can be the teammate's own use). Each has `accounts`, `accounts_with_room`, `usable_now`, `points_left`, `usable_points_left`, `points_per_hour_last_1h`, `weekly_all_points_last_24h`, `points_per_hour_24h`, `hours_left_last_hour_rate`, `hours_left_24h_rate` (points left over that group's own pace; null when no points moved), `next_weekly_reset`, `next_weekly_reset_hours` and `accounts_without_coverage`; `rule` states it. Percent of quota only, never cost. The pool-wide fields above stay as they are. The low headroom alert and the pool reset timer (`docs/pool-reset-auto.md`) read ours only.

## Not in the file

- Plan and rate-limit tier: agent-lb stores `plan_type` as `claude` for every Anthropic account and does not store the tier, so neither is exported.
- A Fable-only count of held resets: agent-lb keeps one banked reset count per account, not one per limit, so the file carries `resets_held` instead.
- Readings come from the latest row in the last 24 hours; a `note` names a weekly reading older than 30 minutes.
- Some accounts get no 5-hour window from Anthropic: neither the usage poll nor the response headers carry `5h` values, only `7d`. Their `five_hour_*` fields are null and the `note` says so.

## Low headroom alert

When `points_by_owner.ours.hours_left_last_hour_rate` (our own accounts' points left over their last hour's points pace) first falls below 3 hours and our next weekly reset is further away than that, the run sends one alert to `factory` through `orch-watch-alert --message`, the route coding-agents-sync's drift alert uses. Teammate-owned accounts are not counted. It fires again only after the figure has gone back above 6 hours and fallen again; the armed state lives in `~/.agent-lb/usage-export-alert.json` on the job host. A failed send exits 1 after the files are written, and the next tick tries again.

## Who asked

`usage-48h.json` also carries `asked_by`: the same 48 hours split by the Rails account that asked for the turn. `asked_by.buckets` has one row per UTC hour, `asker`, provider and model with the bucket metrics; `asked_by.totals` has one row per asker for the 48 hours; `asked_by.records` says how many records were read, rejected and pulled from where; `asked_by.rule` states the rule. Every request no record claims sits under `owner`, so the askers' figures always add up to `totals`. Asker ids are opaque Rails account ids; the file holds no names.

A request belongs to an asker when its `client_session_id` equals a record's `session` and its start time falls in the record's `[start, end)`. When turns of one session overlap (a steer during a running turn), the turn that started last wins from its start on. A record with `end` null counts as running until the run time, at most 12 hours after its start. For each `turn` the last record read wins, so a bridge writes one line when the turn starts and one when it ends.

### Turn records

Each machine whose Rails chat bridge sends turns into the owner's threads appends JSON lines to `~/.agent-rails/asked-by/<any name>.jsonl`; the job pulls `ax102:.agent-rails/asked-by` and `studio:.agent-rails/asked-by` (set `AGENT_LB_ASKED_BY_SOURCES` at install to change them) with rsync into `~/.agent-lb/asked-by/<host>/` each run. A failed pull keeps the last copy and is named in `asked_by.records.pulled`. Write records only for turns a speaker other than the owner asked for.

```json
{"v":1,"turn":"<T3 run id>","session":"<provider native session id>","thread":"<T3 thread id>","asker":"<speaker.account>","seat":"developer","start":"2026-11-10T00:01:02.345Z","end":null}
```

- `session` is the id agent-lb stores as `client_session_id`: the Claude Code session id T3 starts the thread with (`--session-id`, kept on `--resume`), which T3 keeps as `nativeThreadRef.nativeId` on the thread's provider thread.
- `start` and `end` are the T3 run's `startedAt` and `completedAt` (RFC 3339 with a zone), not the time the message was queued.
- `turn` and `asker` match `[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}`; `asker` may not be `owner`. Lines that fail are counted in `rejected_lines`.
- Requests the turn's work makes in other sessions (delegated children, other threads) are not claimed.

## Where it runs

The primary is ax42 since 2026-10-27. The job runs there from the followed runtime (`~/.agent-lb-primary/runtime/agent-lb`) and pushes both files to `ax102:/home/jobs/.agent-rails/factory/agent-lb-export/`. On the target host itself it writes there directly.

```sh
~/.agent-lb-primary/runtime/agent-lb/scripts/install-usage-export.sh
systemctl --user list-timers agent-lb-usage-export.timer
tail ~/.agent-lb/usage-export.log
```

Each log line names the bucket count, file sizes, wall time and CPU time of the run (about 1 s wall and 0.3 s CPU on 2026-11-09).
