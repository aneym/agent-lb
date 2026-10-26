# Burn audit and pacing: what we learned (2026-10-26)

How to find out where Opus and Sol quota goes, how to read the pace numbers, and what we stopped and why. Usage is always stated in quota units (share of a pool's weekly quota, % of one account's week, burn ÷ sustainable, requests or tokens), never in dollars. `request_logs.cost_usd` is an API-price estimate. It is not spend on subsidized subscriptions.

## Reading request_logs for attribution

- Columns that name a source: `client_session_id`, `caller_machine`, `caller_seat`, `useragent_group`, `lane`, `job_id`, `dispatch_id`.
- `requested_at` is naive UTC. Filter with `(now() at time zone 'UTC') - interval '...'`, not `now() - ...`.
- For Opus, `cached_input_tokens` duplicates `cache_read_tokens`. Count one of them, not both.
- `input_tokens` excludes cache reads, so it shows the uncached volume. Cache hit rate is `cache_read / (cache_read + input)`.
- A quick read-only helper on Studio reads `AGENT_LB_DATABASE_URL` from `~/Library/LaunchAgents/com.aneyman.agent-lb.plist` inside Python and never prints it. Put SQL in a file and pass `"$(cat q.sql)"`; inline quoting through ssh breaks on `'`. Quote the `"window"` column, because `window` is reserved.

### Who is who in the logs

| Source | How it shows up |
|---|---|
| T3 Code Claude thread | `caller_seat = t3-code`, UA `claude-cli`, session id is the T3 Claude session |
| T3 Code Codex thread | UA group `T3` (originator "T3 Code"), no seat, no thread id |
| Sol called from inside a Claude session (codex-companion, gpt-* and codex-* wrappers) | UA `claude-cli` with the parent Claude session id, model `gpt-6.1-sol` |
| opus-seat subagents | `caller_seat = opus-seat` |
| ax42 lane leads (lane-host → fstate-supervise → claude) | no seat or lane: `ANTHROPIC_CUSTOM_HEADERS` carried only `x-claude-session-id`, although the process env had `FACTORY_LANE` |
| Rails CoS CLI, Claude Desktop sessions | no seat; the session id is the only handle |

Attribution gaps to close: lane-host should send `x-agent-lane` and a seat from `FACTORY_LANE`, T3 Codex threads should send a thread id, and wrappers should tag Sol calls with the calling seat so they stop landing on the parent session. One ax42 `Python-urllib/3.14` script sent bursts of about 330 calls an hour with no seat or session, and we never traced it.

## What burned (about 28h before the stop)

Opus share: ax42 lane leads ~27%, Claude Desktop sessions on Studio ~25% (one overnight "cutover planner" alone ~14%, a design-council hill climb ~5%), opus-seat subagents ~15%, the Rails CoS CLI ~8%, Studio lane leads ~8%, T3 threads ~2%.

Sol share: Sol inside Claude sessions ~40%, herdr Codex panes on Studio (orchestrators, fixers, QA) ~35%, codex_exec seats ~11%, T3 under 1%.

Most of it was legitimate active work, not idle loops; the orchestrators were busy, not spinning. The waste patterns we confirmed:

- **Autocompact thrash.** Leads launched with a low `--autocompact` (180000) compacted repeatedly. Leave headroom above the working prompt size (leads averaged 80-145k with peaks near 300k).
- **Uncached reviewers.** Design-council Astra reviewers hit cache on about 33% of input against 87-99% everywhere else, because each call put images and per-piece content before the rubric. Put the stable rubric first and the varying content last.
- **Retrying into a 429.** The same reviewers kept calling Astra while its pool was quota-exhausted and returned no score. Skip a seat whose pool is in a 429 window.
- **No early stop.** The hill climb ran every piece to its 6-round cap; its "stop when every reviewer is at 9 or more" rule never fired. Stop after two rounds without a score gain.
- **Opus as a poller.** An overnight "poll every 20 minutes" agent ran as an Opus lead turn. Periodic checks belong in a cheap seat or a script that wakes the lead only on change.
- **Agents that wake on other agents.** The Rails CoS never idled more than 5 minutes because task notifications and teammate messages woke it around the clock.

## The stop (2026-10-26, ~18:20-18:35Z)

Alex: "we're moving everything to be within t3 code now. killing everything else." Everything outside T3 Code was stopped on Studio and ax42. Snapshots, the exact unit and job lists with their prior state, killed processes, session ids and undo steps are in `~/.agent-lb/audits/factory-stop-2026-10-26/` (`RESUME-STOP.md`) on both machines.

Kept running: T3 Code, the agent-lb service and menubar, databases, tunnels and ingress, disk/git/metrics jobs, dashboards, doc and web servers, CI runners, and the rails.so feed relays.

Where respawners live (all had to be stopped, or agents came back):

- ax42 systemd user units: `factory-*` timers (decide, ready-worker, lane-stall, lane-wake-watch, board, worker-reap, sentinel), `orch-fallback-peer`, `thread-memory`, `loop-shadow-log`, `agent-rails-aged-prs`, `lane-replica`, `herdr.service`, `standby-lease`, `admit-dial`, `rails-host-*` and their keepers. Lanes run in `tmux -L lane-<name>` sockets.
- Studio launchd: the `com.open-factory.*` dispatchers and loops, `com.agent-rails.*` (including `lane-revive`, `vpd.dispatch`, `thread-memory`, and the `prod-chat-canary-host`, `rails-chat-qa-host` and `stress-solo-host` Rails hosts), `com.aneyman.herdr-*`, `com.aneyman.quick-dispatch`, `com.aneym.rails-host*` and keepers, `io.agent-rails.*` comment and plan bridges, `com.factory.ax42-lane-sync`, `org.open-factory.nomad`. The `lume` VM `herdr-qa` alone took about 2.5 cores.
- Use `launchctl disable` then `bootout` so a reboot does not bring jobs back; `systemctl --user disable --now` on ax42.

Gotchas:

- Telling T3 processes apart: on ax42 they sit in the `app-t3code.slice` cgroup (`/proc/<pid>/cgroup`); on Studio an ancestor's args contain `/.t3/`. Process names alone do not work: T3 shows as `node-MainThread`, and T3 launches Claude with `--settings ~/.claude/launch/...json` like other launchers do.
- `pgrep -f '<pattern>'` run inline matches the shell running it. A kill loop built that way killed its own shell. Put the loop in a script file so the pattern is not on its command line. Agents may not `pkill` by pattern (kill-guard); kill by pid.
- In this shell a space-separated string variable does not split into arguments (`systemctl` got one long unit name). Use a bash array.
- Rails host keepers respawn harness processes after a kill. Boot out the host and its keeper job first, then kill the leftovers.

Effect on Studio: load average 549 → 26-70 (the remainder is T3 threads running git, rg and du, plus the T3 server at ~40% CPU), CPU idle 1% → 6-39%, processes 2,210 → ~1,300, compressed memory 42 GB → 20 GB, free memory 8.7 GB → 44 GB. ax42 load ~22 → 6. After the stop, about 95% of requests came from T3.

## Reading pace right after a change

`/api/pools/pace` uses a 2h fast burn (`b_fast`) and a 24h slow burn, so it lags a change by up to two hours. For a quick read, measure burn straight from `usage_history`: for each account, sum the positive rises in the weekly `used_percent` within the same `reset_at` over the window, divide by the span in hours, and add the accounts up (weekly rows are `"window" = 'secondary'` or `window_minutes = 10080`). Compare with the same calculation for an earlier window, then scale the pace multiple by that ratio, because the raw sum skips the per-plan factors the pace service applies. Claude readings are whole percents, so a 30-minute Claude read is good to about ±25%.

Thirty minutes after the stop: Claude burn fell ~47% (~11.5 → ~6% of an account's week per hour; ~3.2× → ~1.7× sustainable, dry in ~30h against a refill at 41h). Codex fell ~57% (~19 → ~8% per hour; ~16.6× → ~7×, dry in ~20h against a first refill at 155h, where only ~1.2% per hour lasts). One T3 Claude thread calling Sol through wrappers made more than half of the remaining Sol traffic.

## Pacing model: backtest result (not built yet)

A backtest over 746 hourly points (2026-09-25 to 10-26) favoured:

- a refill-aware projection through each account's own resets, run to raw zero;
- burn as a 6h-half-life EWMA of hourly consumption, carrying the last reading forward until reset;
- an 80% band from log-ratio residual quantiles (q10/q90 over the last 336 points, minimum 48), shown in the tooltip;
- the multiple shown with hysteresis (appear above 1.25×, hide below 1.0×), and "dry in ~Xh" shown when the projection is within 24h and hidden past 36h, rounded to whole hours under 12h, to 3h steps up to 48h, and to days beyond.

For Claude it raised alarm precision from 0.58 to 0.85 and cut false alarms from 26% to 7%. Codex is dominated by unscheduled resets about every 40h, which no burn model predicts.

## Menubar notes

- The weekly pace card shows burn ÷ sustainable per pool on a log scale from ¼× to 4×, with 1× at the centre. The status icon shows the worst over-pace multiple. No dollar figures anywhere.
- Offscreen render check: copy `Sources` except `AgentLBApp.swift` into a temp SwiftPM package on Studio and render `RootView` with `ImageRenderer`, a live `AppState()` and `await state.refreshNow()`. The accounts list renders empty offscreen.
- `make install` over ssh boots the app out but does not relaunch it. Run `launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.aneyman.agentlb.menubar.plist` afterwards. `AgentLBSourceRevision` in the installed plist records the commit.
