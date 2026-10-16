## Context

Seat state lives in `~/.agent-lb/seats/state.json` (`SEAT_HOME`), which `clients/seat` writes under `state.lock`. The registry is `accounts.json`. Live accounts on 2026-10-16: `cursor-main` (login, Pro+ identity, Ultra override), `cursor-gmail` (api-key, Ultra), `devin-main` (data-dir, Devin Pro), `devin-kinetic` (disabled), and `muse-main` (login). `cursor-gmail` holds `cursor-other` limit `until 2026-10-30T00:00:00Z`, which is Oct 29, 8:00 PM ET. That explains "out until Oct 29".

## Decisions

1. **Collection lives in `clients/seat`, and the server schedules it.** `seat` already keeps custody of every seat credential (Cursor key files, `CURSOR_CONFIG_DIR`, Devin data dirs, Muse login). A second resolver in the server would duplicate the money-path credential code. New command: `seat quota [--vendor V] [--account A] [--json]`. It writes `accounts.<id>.quota` under the existing state lock and prints a credential-free summary. The server's `SeatQuotaScheduler` (`app/modules/pools/seat_quota_scheduler.py`) runs it every `AGENT_LB_SEAT_QUOTA_INTERVAL_S` seconds (default 300, plus or minus 10 percent jitter), single-flight, with a 60 s timeout. It uses `AGENT_LB_SEAT_BIN` (default `~/.agent-lb/bin/seat`, then `clients/seat` in the checkout). `seat run` refreshes the quota of the account it used after the vendor CLI exits. That refresh is best effort and never changes the run's exit code.
2. **Vendor sources.** These are the only calls allowed.
   - Cursor: `POST https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage` and `/GetPlanInfo`, body `{}`. Headers: `Authorization: Bearer`, `Content-Type: application/json`, `Connect-Protocol-Version: 1`, `x-cursor-client-type: cli`. A login account reads the CLI access credential from the macOS keychain with no UI. If that read needs interaction, the result is `credential_unavailable`. An api-key account sends its user key as the bearer once. A 401 or 403 means `auth_unsupported`, and the existing estimate is used. Mapping: `planUsage.autoPercentUsed` goes to window `cursor-models` (Auto, Composer, Grok), and `planUsage.apiPercentUsed` goes to window `cursor-other`. Resets come from `billingCycleEnd`. Window kind comes from `GetPlanInfo.includedUsagePeriod`: `monthly` by default, `weekly` when the plan says weekly. Accounts that share a `plan` share one reading.
   - Devin: `POST {api_server_url or https://server.codeium.com}/exa.seat_management_pb.SeatManagementService/GetUserStatus`. Headers: `Content-Type: application/json` and `Connect-Protocol-Version: 1`. The body is `{"metadata": {"apiKey": <windsurf_api_key>, "ideName": "agent-lb", "ideVersion": <seat version>, "extensionName": "agent-lb", "extensionVersion": <seat version>, "locale": "en"}}`, and the key comes from that account's `credentials.toml`. Windows: `daily` (skipped when `hideDailyQuota` is true) and `weekly`. Used percent is `100 - *QuotaRemainingPercent`. Resets come from `*ResetAtUnix`. The plan label comes from the plan info.
   - Muse: no network call. `POST /muse-code/key` mints an inference key, so it is never called. The Muse adapter records a `SubscriptionUsage` frame (`usage/changed`, or a `usage` member) whenever a seat run's JSONL carries one. Windows are `session` (from `window.windowDurationMins`) and `weekly`. The source is `vendor_observed` and `observedAt` is `observedAtMs`. When the stream never carries the frame, Muse quota is `unavailable` with reason `not_reported`. Agents never read Alex's Muse keychain item or start a Muse host just to read usage (floor: Alex's own login, ask `ub_vaxptt`).
3. **Readings are never fabricated.** A missing reading is `null` with a reason. A failed refresh keeps the previous reading with its old `observedAt`. A reading counts as stale after 15 minutes for `vendor` and after 12 hours for `vendor_observed`. Unknown tokens and cost are `null` with `tokensKnown: false`. They are never summed as 0.
4. **Routing does not change.** `headroomPercent`, `aggregateRemainingPercent`, `eligibleAccounts`, `status`, cooldowns and seat selection keep today's semantics. The new `windows` and `quota` are for display and advisory use. Changing routing on vendor quota would be a separate change.
5. **Status uses the proxy account vocabulary,** so both clients reuse their existing row presentation: `active`, `rate_limited` (an account or pool cooldown is live, or any window is at 100 percent or more), `paused` (disabled), `reauth_required` (`authOk` is false). `statusResetAt` is the earliest moment that would clear it.
6. **Grok is a model, not a vendor.** The `cursor-models` window's label is "Cursor models · Grok, Composer". Activity breaks down per model, so Grok runs are visible inside the Cursor scope.
7. **Times.** The API returns UTC ISO 8601. Clients show resets and "as of" in ET (`America/New_York`).

## Contract

The JSON uses camelCase, as `DashboardModel` serializes it. Fields marked new are additive, and existing fields keep their names and meaning.

### `GET /api/pools/seat-accounts`

```
{
  stateUpdatedAt: datetime|null,
  source: "seat_state"|"seat_state_stale"|"missing",
  quotaRefreshedAt: datetime|null,                      new: last scheduler or run refresh
  accounts: [SeatAccount]
}
SeatAccount = {
  id, vendor: "cursor"|"devin"|"muse", auth, enabled, authOk: bool|null, authCheckedAt,
  tier, plan, cycleDay, cooldownUntil, cooldowns: {pool: datetime},
  lastErrorKind, lastErrorAt, lastRunAt, ready, lastDay, daily,
  recentRuns: [{ts, model, tokensIn|null, tokensOut|null, cacheRead|null,
                ok: bool|null, wallS: number|null}],             ok, wallS new
  email: string|null,                                   new: identity email; clients apply their privacy mask
  planLabel: string|null,                               new: vendor plan name, else tier
  status: "active"|"rate_limited"|"paused"|"reauth_required",   new
  statusResetAt: datetime|null,                         new
  quota: SeatQuota,                                     new, always present
  today: SeatToday                                      new
}
SeatQuota = {
  source: "vendor"|"vendor_observed"|"estimate"|"unavailable",
  observedAt: datetime|null,
  stale: bool,
  unavailableReason: "not_reported"|"auth_unsupported"|"credential_unavailable"|"vendor_error"|"not_collected"|null,
  primaryWindowId: string|null,                         window with least remaining; drives the row ring
  windows: [SeatQuotaWindow]
}
SeatQuotaWindow = {
  id: "cursor-models"|"cursor-other"|"daily"|"weekly"|"session",
  kind: "session"|"daily"|"weekly"|"monthly",
  label: string,                                        e.g. "Cursor models · Grok, Composer", "Other models", "Daily", "Weekly", "5-hour"
  durationMins: int|null,
  usedPercent: number|null,                             verbatim, may exceed 100
  remainingPercent: number|null,                        clamp(100 - usedPercent, 0, 100)
  resetsAt: datetime|null,
  source: "vendor"|"vendor_observed"|"estimate"
}
SeatToday = {                                           rolling 24 h, same window as lastDay
  runs: int, ok: int,
  tokensIn: int|null, tokensOut: int|null, tokensKnown: bool,
  valueUsd: number|null, valueSource: "estimate"|null,  Cursor list-price estimate; null for Devin and Muse
  models: [{model, runs, tokensIn|null, tokensOut|null}]
}
```

The Cursor estimate (`source: "estimate"`) is used only when there is no vendor reading for that account. It reuses `cursor_budget_pools` pricing and the vendor-limit override.

### `GET /api/pools`

Every `kind` in `cli_seat` and `cli_seat_budget` gains:

```
windows: [{ id, kind, label, remainingPercent: number|null, usedPercent: number|null,
            resetsAt: datetime|null, measuredAccounts: int, source: "vendor"|"vendor_observed"|"estimate"|"mixed"|null }] | null
quotaSource: "vendor"|"vendor_observed"|"estimate"|"mixed"|"unavailable"   new
```

`remainingPercent` is the mean over measured accounts in the pool, with plan-shared Cursor accounts counted once. `resetsAt` is the earliest reset. `percentSource` also accepts `vendor_observed`. The Muse pool keeps `headroomPercent: null` and `observedTokens: null` unless Muse reported a value.

### `GET /api/usage/summary?provider=P`

```
provider: string|null,                                  new: echo; null on the All scope
seatActivity: null | {                                  new: null when P has no seat accounts; on All, every vendor summed
  periodHours: 168, runs: int, ok: int,
  tokensIn: int|null, tokensOut: int|null, tokensKnown: bool,
  valueUsd: number|null, valueSource: "estimate"|null,
  models: [{model, runs}]
}
```

`seatActivity` reads the seat state `daily` buckets for the last 7 ET days. `cost`, `metrics` and the request counts keep their request-log meaning. For `provider=muse` those counts include `/muse-api` request logs, and `seatActivity` adds the seat runs. The counts are not merged.

`contract/seat-usage.example.json` holds one example of each response. Each client's fixtures copy it. The backend test asserts that its serializer produces the same key set.

## Risks

- Keychain reads from a launchd process can raise a dialog. The collector runs `security find-generic-password` with no UI. A prompt counts as a failure, and the end-to-end check compares `desktop-guard-focus` before and after.
- Cursor api-key bearer acceptance is unverified, and the estimate stays the honest fallback.
- Muse probe result (2026-10-16): `has_usage_frame=false`. One successful `clients/seat run` used `--vendor muse --model muse-wonder-1.0 --mode ask --timeout 120`, prompt `reply OK`, `MUSE_NO_AUTO_UPDATE=1`, and the existing seat login. Version: Muse Code 1.4.9 (1.4.9-R6297.1). Raw exec stdout was captured by wrapping the client's `run_stoppable` function in memory; no repository files changed during the probe. The output contained 44 JSONL frames (34,804 bytes). Recursive field-key search found no `usage`, `subs_usage`, `SubscriptionUsage`, `used_percent`, or rate-limit fields. Plain-text matches were only the worktree/branch name `muse-usage`. The seat envelope reported `ok=true`, `tokens_in=null`, `tokens_out=null`, `usage_source="unavailable"`. Check tail: `JSONL frames: 44`; `Matching field paths: []`; `PASS: seat rc=0; subscription-usage frame absent; text matches only worktree/branch muse-usage`. This establishes absence for this run, not every possible Muse execution. No direct key endpoint calls or keychain access. Initial argument validation required an explicit model; no Muse execution occurred on that attempt. The in-memory stdout wrapper was needed because `clients/seat` normally prints a summary envelope. When Muse does not report usage, it shows "Not reported by Muse" with run counts.
