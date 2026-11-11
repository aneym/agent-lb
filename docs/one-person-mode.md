# One-person mode

One-person mode runs agent-lb on a person's own computer over that person's own plans. It never joins a pool: it listens on loopback only, serves no dashboard, runs no federation (no mirror, peer, push or pool sync), and keeps its accounts in its own data dir. It posts a headroom summary in percent to Rails. The default `agent-lb` service is unchanged; this mode only runs when started with `agent-lb solo`.

## Start it

```sh
agent-lb solo import-claude                      # reads ~/.claude/.credentials.json and ~/.claude.json
agent-lb solo serve --port 2456                  # 127.0.0.1 only
```

- `--data-dir` (or `AGENT_LB_SOLO_DATA_DIR`) picks the data dir; the default is `~/.agent-lb-solo`. The shared `~/.agent-lb` is refused.
- `--port` (or `AGENT_LB_SOLO_PORT`) picks the port; the default is 2456. There is no host flag: it binds `127.0.0.1`.
- Clients point at `http://127.0.0.1:2456` exactly as they would at the shared service.

What the mode sets before the app loads (`app/solo_env.py`): the data dir, a SQLite database, the encryption key, state, throttle and archive paths inside it; every federation token and peer URL blank; federation push files pointed at a path that does not exist; metrics and tracing off; the bridge ring and advertise URL dropped. After the app is built (`app/solo_routes.py`) only the proxy routes, `/v1/models` and `/health/live` and `/health/ready` remain. Dashboard, settings, accounts, OAuth, federation, Muse, docs and OpenAPI routes answer 404.

## Claude sign-in import

`agent-lb solo import-claude` takes the OAuth credential Claude Code stores:

- Linux and Windows: the file `~/.claude/.credentials.json` (the default), or any path with `--file`.
- macOS: Claude Code keeps it in the login keychain, item `Claude Code-credentials`. Reading that item asks for a keychain prompt, so agent-lb never reads it. The person exports or pastes the JSON and pipes it in with `--stdin`.

Accepted shapes: `{"claudeAiOauth": {...}}` as Claude Code writes it, or the inner object alone, with `accessToken`, `refreshToken`, and optionally `expiresAt` (ms), `scopes` and `subscriptionType`. A credential without a refresh token, or with scopes that lack `user:inference`, is refused. The account's identity comes from `oauthAccount` in `~/.claude.json` (or `--profile`), so a second import of the same sign-in updates its row; without a profile the id is derived from the refresh token. The account is stored the way an agent-lb Claude OAuth sign-in is (`app/modules/oauth/token_account.py`), tokens encrypted. The command prints the account prefix, plan, status and expiry, never a token.

Both agent-lb and Claude Code refresh the same sign-in. Once agent-lb refreshes it, the copy Claude Code holds can stop working, so the person's Claude Code should send its requests through this agent-lb rather than straight to Anthropic.

## Headroom post

When `AGENT_LB_SOLO_HEADROOM_URL` is set, the server posts the summary at start and every `AGENT_LB_SOLO_HEADROOM_INTERVAL_SECONDS` (default 300, at least 30). The bearer token comes from `AGENT_LB_SOLO_HEADROOM_TOKEN_FILE` or `AGENT_LB_SOLO_HEADROOM_TOKEN`; it is never a command-line flag. `agent-lb solo headroom` prints the same JSON once, and `--post` also posts it once.

`POST <url>`, `Content-Type: application/json`, `Authorization: Bearer <token>`:

```json
{
  "schema": "agent-lb-solo-headroom/1",
  "instance": "<this computer's agent-lb instance id>",
  "generated_at": "2026-11-11T21:34:12Z",
  "lookback_hours": 24,
  "plans": [
    {
      "account": "00000000",
      "provider": "anthropic",
      "plan_type": "max",
      "status": "active",
      "five_hour_used_pct": 100.0,
      "five_hour_remaining_pct": 0.0,
      "five_hour_resets": "2026-11-11T23:03:30Z",
      "weekly_used_pct": 62.0,
      "weekly_remaining_pct": 38.0,
      "weekly_resets": "2026-11-15T21:33:30Z",
      "fable_weekly_used_pct": null,
      "fable_weekly_headroom_pct": null,
      "limits_recorded_at": "2026-11-11T21:33:30Z",
      "usable_now": false,
      "limit_reason": "5-hour window at 100%"
    }
  ],
  "usable_now": []
}
```

- `account` is the first 8 characters of agent-lb's account id, stable across posts.
- Percentages are the latest reading in the last 24 hours, `null` when there is none. No costs or dollar figures are sent.
- `usable_now` and `limit_reason` apply the same rule as the shared headroom export (`app/usage_export_headroom.py`, `blocker` and `exhausted`): blocked statuses, a canceled subscription, a waiting status before its reset, or a 5-hour or weekly window at 100%.
- Any 2xx answer is success. A failed post is logged and retried on the next interval.

Rails does not take this post yet; until it does, point the URL at any endpoint that accepts the JSON above.
