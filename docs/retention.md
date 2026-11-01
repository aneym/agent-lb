# Request log retention

agent-lb keeps raw request rows for 90 days. Older rows are archived to compressed files on a data volume, folded into the `request_log_daily` rollup, and then deleted. Every lifetime or long-range total reads the rollup next to the live rows, so dashboards keep the same answers after a run.

## Policy

| Table | Window | What happens past it |
| --- | --- | --- |
| `request_logs` | 90 days raw, cut at UTC midnight | Archived per day, folded into `request_log_daily`, deleted |
| `request_context_parts` | Follows its `request_logs` row | Archived with the parent day, removed by `ON DELETE CASCADE` |
| `request_log_daily` | Kept | One row per day and dimension set; a few thousand rows per quarter |
| `usage_history`, `additional_usage_history` | Kept | Readers need the latest row per account, window and quota key; reclaim their space by rebuilding indexes |

Soft-deleted request rows (`deleted_at` set when an account is removed) follow the same window. The rollup keeps them under `deleted = true`, and deleting an account updates or removes its rollup rows the same way it updates its raw rows.

The window applies to whole rows. `request_logs` stores no prompts, bodies or traces; its largest column is `unified_headers`, about a quarter of row bytes, so there is no column worth expiring on its own.

## Readers

| Reader | Needs | Source past 90 days |
| --- | --- | --- |
| Accounts page lifetime totals | Sums per account, without warmups, deleted rows or superseded replays | `request_log_daily` (`superseded` marks the older replay copy) |
| API key lifetime totals, list and detail | Sums per key, without warmups or forwarded edge rows | `request_log_daily` |
| Public usage page, up to 730 days | Daily sums by model and provider | `request_log_daily` |
| Reports (daily, by model, by account, active accounts) | Daily sums for any date range | `request_log_daily` joined to accounts for the canceled-subscription filter |
| Dashboard, usage windows, sessions, quota planner, pool share, team windows, API key limits | 30 days or less | Raw rows only |
| Receipts, session detail, verifier job usage, `agent-lb audit` commands | Row detail | Raw rows; older detail lives in the archive and comes back with `restore` |
| Federation usage | 7 days, persisted in `federation_usage_daily` | Raw rows only |

## Commands

```sh
agent-lb retention run --dry-run --archive-dir ~/.agent-rails/archive-pc-wsl/agent-lb-retention --receipt <receipt.json>
agent-lb retention run --apply --archive-dir ~/.agent-rails/archive-pc-wsl/agent-lb-retention --approval <approval record>
agent-lb retention restore ~/.agent-rails/archive-pc-wsl/agent-lb-retention/<archive id>
```

`run` defaults to a dry run: one read-only transaction that reports rows, bytes, rollup rows, the archive estimate and the space a rewrite would free for the 30, 60 and 90 day windows. `--apply` refuses without an approval record, refuses an archive directory on the root volume, and refuses a volume that lacks twice the estimated archive size plus 1 GB or would pass 70% used with it.

## Archive target

On ax42 the archive goes off the box to pc-wsl through the `~/.agent-rails/archive-pc-wsl` mount, under `agent-lb-retention/`. Never `/` (the database volume) and never `/srv`: `/srv/data` and `/srv/cow` share one volume that worktrees need. Any other target must be off the box or on a volume that stays under 70% used after the archive lands.

Apply takes an advisory lock, exports every day before the cutoff in one repeatable-read snapshot to `request_logs/<day>.csv.gz` and `request_context_parts/<day>.csv.gz`, and writes `manifest.json` with a row count and md5 per day plus sha256 of each file and of its uncompressed bytes. It re-reads every file before deleting anything. Each day is then its own transaction: lock the rows, recompute the checksum, insert the rollup rows tagged with the archive id, delete, and check the count. A day that changed since export stops the run with nothing deleted for it.

Restore verifies the files, loads the applied days back through a staging table, checks each day's count, md5 and re-exported sha256 against the manifest, and removes the archive's rollup rows, all in one transaction.

## Reclaiming disk

Deleted rows free pages for reuse inside Postgres after autovacuum; they do not shrink files. Returning the space to the OS needs a rewrite: `VACUUM FULL request_logs` holds an ACCESS EXCLUSIVE lock for the whole rewrite, so proxying stalls on request log writes for its duration; `pg_repack -t request_logs` rebuilds online and locks only briefly at the start and end, but needs the extension installed. Both need free disk equal to the kept table plus its indexes. `REINDEX TABLE CONCURRENTLY` rebuilds bloated indexes without blocking writes.

Measured on ax42 on 2026-11-01, on a full copy after a 90-day apply: `VACUUM FULL request_logs` took 2 min 26 s and shrank the table from 8.96 GB to 7.73 GB. On live, expect a lock of about 2.5 to 3 minutes and plan for 8 GB free on the database volume. A freshly built copy also showed index bloat on live: `additional_usage_history` indexes are 3.30 GB against 2.03 GB rebuilt, and `usage_history` 1.42 GB against 0.77 GB, so `REINDEX TABLE CONCURRENTLY` on those two returns about 1.9 GB with no row loss. `idx_logs_request_status_api_key_session_time` (879 MB) recorded no scans.

## Schedule

`deploy/systemd/agent-lb-retention.timer` runs the apply weekly. It ships disabled; install and enable it only with a destructive-data approval record at the path the service names.
