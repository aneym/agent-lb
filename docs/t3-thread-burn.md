# T3 thread burn

agent-lb knows every T3 Code thread by name and charges each request to its thread, including the thread's subagents, workflow agents, delegated child threads and the Sol calls its Codex wrappers make. That shows which threads use which pool's weekly quota. Usage is always given in quota units: share of a pool's weekly quota, % of one account's week, burn ÷ sustainable, requests and tokens. Dollars never appear. `request_logs.cost_usd` is an API-price estimate. The service uses it only as a relative weight between requests and never returns it.

Spec written 2026-10-26 from real rows on both T3 hosts and prod `request_logs`. Goal (Alex, 2026-10-26): "fold in the t3 updates and stuff so we can truly do analysis on our threads end to end".

## The pieces

1. A registry in agent-lb's database: `t3_threads` and `t3_native_sessions`.
2. A collector on each T3 host. It reads `statev2.sqlite` read-only and posts changed rows to agent-lb.
3. An attribution service. It joins `request_logs` to the registry and splits each account's hourly weekly-quota rise across the requests that caused it.
4. One API route group, `/api/t3/threads`.
5. One CLI report, `agent-lb threads`.
6. One dashboard view, **Threads** (`/threads`).

## Where T3 keeps the native ids

T3 state lives in `~/.t3/userdata/statev2.sqlite` on each host: `/home/jobs/.t3/...` on ax42, which runs the T3 server, and `/Users/aneyman/.t3/...` on Studio, which runs T3 Code desktop. Studio's older `state.sqlite` holds only 3 July test threads and is ignored. The collector opens the file with `file:<path>?mode=ro` and `uri=True` and sets `PRAGMA query_only=1`. The server writes live through WAL.

| What | Table | Field |
|---|---|---|
| Thread, title, timestamps | `orchestration_v2_projection_threads` | `thread_id`, `title`, `project_id`, `default_provider`, `created_at`, `updated_at`, `archived_at`, `deleted_at` |
| Parent and root | same table, `payload_json` | `$.lineage.parentThreadId`, `$.lineage.rootThreadId`, `$.lineage.relationshipToParent` (`subagent` or null) |
| Who made it | same, `payload_json` | `$.createdBy` (`user`, `agent`, `system`), `$.creationSource` (`web`, `mcp`, `provider`, `server`), `$.modelSelection.model` |
| Native session id | `orchestration_v2_projection_provider_threads`, `payload_json` | `$.nativeThreadRef.nativeId`, with `driver` (`claudeAgent` or `codex`), `provider_instance_id` and `thread_id` |
| Project name | `projection_projects` | `project_id`, `title`, `workspace_root` |

`provider_thread_id` and `provider_session_id` are T3-internal ids (`provider-thread:...`, `provider-session:...`). Neither is the native id. Key on `driver`, not `provider`: Studio has provider instances such as `factory-shelf`, `agent-raise` and `agent-factory` whose `driver` is `claudeAgent`.

`nativeId` is the value agent-lb logs as `client_session_id`. For a Claude thread it is the native Claude session id (`x-claude-session-id`). For a Codex thread it is the native Codex thread id, a UUIDv7-like value such as `01a17983-...`. Claude subagents, workflow agents and Sol calls made through Codex wrappers inside a Claude thread all carry the parent's session id, so they join to the parent thread directly.

Checked on real rows, 2026-10-26 ~19:30Z:

| | ax42 | Studio |
|---|---|---|
| Threads | 93 | 852 |
| Provider-thread rows | 49 | 62 |
| Rows with a native id (all `strength: strong`) | 43 (30 Claude, 13 Codex) | 62 (56 Claude driver across 6 instances, 6 Codex) |
| Pending rows (no native id yet) | 6 delegated Codex children | 0 |
| Threads with more than one native id | 0 | 0 |
| Native ids also seen as `client_session_id` in prod, last 7 days | 43 of 43 | 61 of 62 |

All 105 native ids are distinct across both hosts. Every thread without a native id is a child: a provider-native Claude subagent or workflow agent whose traffic runs in its parent's session, a delegated child still pending, or one of 3 legacy-import threads on Studio. `lineage.rootThreadId` matched a walk up `parentThreadId` for all 93 + 858 threads; depth is 0 to 3.

A native id exists only after a thread's first run, because until then T3 holds a `pending:` id. Registration can lag a request by one collector tick. Attribution joins at read time, so a late registration still claims its earlier requests.

## Parent, child and subagent links

- **Root.** Store `root_thread_id = lineage.rootThreadId`, or the thread's own id when lineage is empty. Every rollup groups by `(host, root_thread_id)`.
- **Delegated child threads** (`thread:delegated-task:...`, and `orchestration_v2_projection_subagents.origin = 'app_owned'`). These have their own native session, so their requests match their own registry row and roll up to the root. The view shows them as children.
- **Provider-native subagents and workflow agents** (`origin = 'provider_native'`; titles like `Workflow · ...`, `spec`, `judge`). These have no native id, and their requests carry the parent's session id. They are registered so the view can say "includes N subagents and workflow agents", but their requests stay with the parent session. agent-lb cannot split traffic among them.
- **Sol through Codex wrappers in a Claude thread.** UA `claude-cli`, model `gpt-6.1-sol`, the parent's session id. These match the parent thread and land in the Codex pool. The view shows them as "Sol via wrappers".
- `orchestration_v2_projection_context_transfers` (`subagent_result` rows) duplicates lineage and is not used.

## Schema (additive)

Migration `20261026_200000_add_t3_thread_registry` (down_revision `20261026_090000_add_postgres_quota_seek_indexes`) adds:

`t3_threads`
- `host` String(32), `thread_id` Text, primary key `(host, thread_id)`. Some subagent thread ids run past 300 characters.
- `project_id` String(64), `project_title` Text, `workspace_root` Text, `title` Text, `provider_instance` String(64), `model` String(64): all nullable.
- `parent_thread_id` Text nullable, `root_thread_id` Text not null, `relationship` String(32) nullable, `created_by` String(16), `creation_source` String(16).
- `created_at`, `updated_at`, `archived_at`, `deleted_at`: naive UTC DateTime, as T3 reported them.
- `first_seen_at` and `last_seen_at`: server time.
- Index `ix_t3_threads_root` on `(host, root_thread_id)`.

`t3_native_sessions`
- `native_id` String(64) primary key, `host` String(32), `thread_id` Text, `driver` String(32), `provider_instance` String(64), `first_seen_at`, `last_seen_at`.
- Index `ix_t3_native_sessions_thread` on `(host, thread_id)`.

Migration `20261026_200100_add_request_log_client_session_index` is Postgres only and runs in an autocommit block like `..._090000`: `CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_logs_client_session_time ON request_logs (client_session_id, requested_at DESC) WHERE client_session_id IS NOT NULL`. Mirror it in `app/db/model_indexes.py` with `.ddl_if(dialect="postgresql")` so schema-drift checks stay clean. Models go in a new `app/db/t3_models.py`, imported from `app/db/__init__.py`. Nothing existing is altered or dropped. Rows are never deleted. A thread T3 deletes keeps its row with `deleted_at` set, so its history stays named.

## Ingest

`POST /api/t3/threads/sync`, behind the same dashboard dependencies as `/api/pools/reservations` (`validate_dashboard_session`, `set_dashboard_error_format`).

Body: `{"host": "studio"|"ax42", "threads": [...], "nativeSessions": [...]}`. `host` must match `^[a-z0-9-]{1,32}$`. A call carries at most 2,000 threads and 2,000 sessions; the collector chunks to stay under that. The response is `{"threads": n, "nativeSessions": m}`.

Writes are idempotent upserts. Threads key on `(host, thread_id)`, using dialect insert `on_conflict_do_update` as in `app/modules/proxy/sticky_repository.py`. Every field is overwritten, because titles get regenerated, and `last_seen_at` is bumped. Native sessions key on `native_id`. A native id that shows up under a different thread is overwritten and logged at warning. Resending the same rows does nothing harmful.

Transport:
- **Studio** posts to `http://127.0.0.1:2455` (local, trusted).
- **ax42** posts to `http://127.0.0.1:2457`, the `agent-lb-proxy` Caddy hop that goes only to the primary `https://studio.tailf266ac.ts.net:2455`. Studio admits ax42's tailnet address without a key. Checked: `GET /api/pools` returns 200 through 2457, and an empty `POST /api/pools/reservations` returns 422 rather than 401. Do **not** use 2456. Its default route fails over to the ax42 edge on 2458, which has its own database. If Studio cannot be reached, the post fails, the collector exits 1 without moving its cursor, and the next tick retries.

The collector never sends message text, prompts or attachments. It sends titles, project names, workspace paths, ids and timestamps only.

## Collector

`scripts/t3-thread-collector`: stdlib Python, compatible with 3.9 (Studio's `/usr/bin/python3` is 3.9.6 with SQLite 3.51; ax42 runs 3.12 with 3.45; both have JSON1).

- Flags: `--host` (required), `--db` (default `$T3CODE_HOME/userdata/statev2.sqlite`, else `~/.t3/userdata/statev2.sqlite`), `--base-url` (default `$AGENT_LB_BASE_URL`, else `http://127.0.0.1:2455`), `--state` (default `~/.agent-lb/state/t3-collector.json`), `--full`, `--dry-run`. `--dry-run` prints counts only and posts nothing.
- Each run reads every project, every native-id row (`provider_threads` where `$.nativeThreadRef.nativeId` is set), and threads with `updated_at >= cursor - 15 min`. It makes a full pass when there is no state file or the last full pass is over 6 hours old. The cursor is the largest `updated_at` that was posted successfully.
- Studio's `statev2.sqlite` is 678 MB, mostly turn items and messages. The collector touches only the threads, provider-threads and projects tables, so a run takes well under a second.

## Attribution

Lives in `app/modules/t3_threads/service.py`. The pure apportioning math goes in `attribution.py`, so a table-driven test can hold its edge cases.

1. **Window.** 24 hours or 7 days, ending now. `requested_at` is naive UTC.
2. **Requests.** One grouped SQL query over `request_logs` (with `deleted_at` null) groups by account, provider, hour, `client_session_id`, model, `caller_seat` and `useragent_group`. It sums request count, input, output and cache-read tokens, and a weight. Tokens split as `prompt_tokens.prompt_split` does: cache-read falls back to `cached_input_tokens`, and non-Anthropic input excludes it. Seven days (about 1.1M rows, 36k groups) took about 5 s. Cache the result for 60 s, as `pace_service` does.
3. **Weight.** `cost_usd` when set. When it is null (today only `claude-haiku-5-5`, 245 successful rows in 24 h), use token-equivalents (input + 5 × output + 1.25 × cache-creation + 0.1 × cache-read) times the window's priced weight-per-token-equivalent for that provider. Failed requests with no tokens weigh 0.
4. **Quota rise.** For each pool account, take 5-minute samples from `sampled_usage` and keep weekly rows with `weekly_row`; both are in `pace_service.py`. Within one `reset_at` window (`same_window`), count only rises above the running high of `used_percent` (Claude flickers ±1 point); sum them per hour. Multiply by the account's plan factor (`PLAN_FACTORS`: Claude 1, Codex pro 1, plus 1/18). The unit is "% of one full account's week".
5. **Apportioning.** Split each account-hour's rise across that account's requests in the same hour, in proportion to weight. If an hour has a rise but no requests, use the previous hour's requests on that account. Otherwise count it as **unexplained**.
6. **Labels.** Each request group gets one label:
   - **T3 thread**: `client_session_id` matches `t3_native_sessions`. Roll up native id → `(host, thread_id)` → `root_thread_id`.
   - **T3, unregistered**: no match, but `caller_seat LIKE 't3-%'` or `useragent_group = 'T3'`. In practice these are one-shot T3 calls: title generation on Haiku, and short Sol or Opus calls whose id is in no T3 table.
   - **Outside T3**: everything else, broken down by seat in the totals only.
7. **Units returned.** Per pool: `accountWeekPct` (the summed factored rise), `poolSharePct` (`accountWeekPct` ÷ (100 × Σ plan factors of the pool's non-excluded accounts) × 100), `paceMultiple` (the thread's rise per hour over the window ÷ the pool's `b_star` from `/api/pools/pace`), plus requests and input, output and cache-read tokens. Pools are `anthropic` (labelled Claude) and `openai` (labelled Codex, which carries Sol). Kimi is left out; it has no T3 traffic.

Claude's weekly readings are whole percents. A thread's share over 24 hours is reliable, but a 1-hour slice is good only to roughly ±1 point of one account's week.

## API, CLI and UI

Route group `/api/t3/threads` (`app/modules/t3_threads/api.py`):

- `POST /sync`, described above.
- `GET /burn?range=24h|7d&host=&pool=&root=` returns:
  - `generatedAt`, `windowHours` and `host`. With `host=`, `t3Threads` and the rows cover that host; the other buckets cover all hosts, as the UI and CLI say.
  - `pools`: for each pool, `poolSharePct` and `accountWeekPct` for `t3Threads`, `t3Unregistered`, `outsideT3` and `unexplained`, plus `matchedRequestPct`.
  - `threads`: one row per root, sorted by combined `poolSharePct` with the highest first. Each row has `host`, `rootThreadId`, `title`, `projectTitle`, `createdAt`, `lastRequestAt`, `archived`, `deleted`, `subagentCount`, `nativeSessions` (`[{nativeId, driver}]`), `pools.{anthropic,openai}.{poolSharePct, accountWeekPct, paceMultiple, requests, inputTokens, outputTokens, cacheReadTokens}`, `models` (requests by family: Opus, Haiku, Codex, and Sol via wrappers for Sol from a Claude caller), and `children`. Each child is a delegated thread with its own native session, in the same shape.
  - With `root=`, the response holds that one root and adds `hourly`: per pool, `accountWeekPct` per hour.
- No field carries `cost_usd` or any value derived from it except the shares. A schema test asserts that no key contains `cost` or `usd`.

`main.py` is 19,919 bytes, against a 20,000-byte cap. Before adding `t3_threads_api.router`, fold its dashboard `app.include_router(...)` lines into the existing `for router in (...)` loop. That changes no behavior and frees about 400 bytes.

CLI: `agent-lb threads [--range 24h|7d] [--host studio|ax42] [--pool anthropic|openai] [--limit 20] [--json] [--base-url URL]`. It lives in `app/t3_threads_cli.py`, is registered in `app/cli.py` through the `add_parser` pattern, and fetches through `status_cli.get_status_json`, as `pace_cli` does. It prints one line per root: title (cut to 48 characters), host, Claude % of pool, Codex % of pool, requests, children count. The footer gives matched-request coverage and the T3-unregistered, outside-T3 and unexplained shares for each pool. Example line: `P1 · Recruiter as its own agent app  ax42  Claude 4.1%  Codex 0.2%  1,786 req  10 sessions`.

Dashboard: a new top-nav entry **Threads**, placed after Usage. Route `/threads`; files `frontend/src/lb/pages/threads/threads-page.tsx` and `threads.css`; the zod schema and hook go in `frontend/src/lb/api/threads.ts`.

- The page has one header: `PageHead` "Threads", a range picker (24h, 7d) and a host filter (All, Studio, ax42). No other chrome.
- One quiet line per pool: "T3 threads used 31% of the Claude pool's weekly quota in the last 24 hours. Unregistered T3 0.4%, outside T3 6%, unexplained 1%." Beside it, a thin stacked bar built with the dataviz skill's neutral palette.
- The table has one row per root thread. Columns: Thread (title, then project · host in muted text, plus "archived" in muted text when set), Claude pool (% of weekly pool, then % of an account's week below it), Codex pool (same), ×sustainable, Requests, Last active. It sorts by any column.
- Expanding a row shows its delegated children in the same columns, the model split, "includes N subagents and workflow agents", and each native session linked to `/sessions/<nativeId>` (trace) and `/usage?session=<nativeId>` (receipts).
- Design rules: no dollar figures, no red badges or count circles, no fingernail cards, no nested boxes. Light and dark screenshots plus an Opus design pass before acceptance.

## Install

Repo files:
- `scripts/t3-thread-collector`
- `scripts/install-t3-thread-collector.sh`, which branches on `uname`:
  - **Darwin (Studio).** Copies the script to `~/.agent-lb/bin/t3-thread-collector` and writes `~/Library/LaunchAgents/com.aneyman.agent-lb-t3-collector.plist` with `ProgramArguments` = `/usr/bin/python3 <bin> --host studio`, `StartInterval` 120, `RunAtLoad`, `StandardErrorPath` `~/.agent-lb/state/t3-collector.err.log`. Then `plutil -lint`, `launchctl bootout` (ignoring a miss) and `launchctl bootstrap gui/$(id -u)`. This follows `install-account-health-watch.sh`.
  - **Linux (ax42).** Copies the script to `~/.agent-lb/bin/t3-thread-collector` and writes `~/.config/systemd/user/agent-lb-t3-collector.service` (`Type=oneshot`, `Environment=AGENT_LB_BASE_URL=http://127.0.0.1:2457`, `ExecStart=/usr/bin/python3 %h/.agent-lb/bin/t3-thread-collector --host ax42`, `Nice=10`, `TimeoutStartSec=60`) and `agent-lb-t3-collector.timer` (`OnBootSec=60`, `OnUnitActiveSec=120`). Then `systemctl --user daemon-reload` and `systemctl --user enable --now agent-lb-t3-collector.timer`.
- Commands: on Studio, `sh scripts/install-t3-thread-collector.sh` from the runtime checkout after deploy. On ax42, the same script from a checkout of `main`. Both are run once by the deploying agent, after the migration is live.

## Measured coverage (prod, last 24 hours, 2026-10-26 ~19:30Z)

The dry join of the native-id map from both hosts against `request_logs` was read only. "T3 traffic" means any row that matches the map, has seat `t3-*`, or has UA group `T3`.

| Host | T3 requests | Matched to a named thread | By weight |
|---|---|---|---|
| ax42 | 4,661 | 4,631 (99.4%) | 99.6% |
| Studio | 6,992 | 6,965 (99.6%) | 99.6% |
| Both | 11,653 | 11,596 (99.5%) | 99.6% |

The 57 requests left over are one-shot T3 calls: Haiku title generation, and single Sol or Opus calls whose ids appear in no T3 table. They are reported as "T3, unregistered".

Since the factory stop (18:40Z), 92% of all requests by count (95% by weight) match a named thread. The remainder is seat-run subprocesses that T3 threads start (`opus-seat` 122, `codex-verifier` 77, `gpt-implementer` 34, `pi` 67). Each has its own session id and no link to the thread that started it.

The heaviest roots in 24 hours by share of T3 weight within each pool:
- Raise (Studio): 44% of T3 Codex.
- P1 · Recruiter as its own agent app (ax42): 26% of T3 Claude.
- P1 · Close out the 1,400 loose branches (Studio): 14% of T3 Codex.

## Tests

Load `~/.agents/skills/test-audit/SKILL.md` first.

- One integration test drives the real app with the test SQLite database: post a sync of two hosts' fixture rows, including a delegated child, a provider-native subagent and a re-post with a changed title. Then insert `request_logs` and `usage_history` rows and call `GET /api/t3/threads/burn`. Assert the root rollup, child placement, unregistered and outside-T3 buckets, pool shares that add up to the measured rise, and that no key contains `cost`.
- One golden test runs the collector against a small redacted `statev2` fixture built from real rows, with `--dry-run`, comparing its printed counts.
- One table-driven unit test for `attribution.py` alone, because it is pure apportioning math with edge cases: no requests in an hour, a reset inside an hour, null weights, and a plus account's factor.

## Not in scope, follow-ups

- Seat-run subprocesses started inside a thread inherit `CLAUDE_CODE_SESSION_ID`. `seat-run` (agent-rails) should forward it as `x-agent-lb-parent-session`, and agent-lb should store it, so that `opus-seat` and verifier traffic rolls up to the thread.
- Price `claude-haiku-5-5` in the pricing table so the fallback weight is no longer needed.
