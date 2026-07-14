# Getting Started — Agent Runbook

This is the canonical setup walkthrough for **coding agents** (Claude Code, Codex CLI, etc.)
onboarding a teammate's Mac. The goal: a running local agent-lb service with the teammate's
LLM accounts connected and their CLI tools routed through it — **without the human ever
opening a dashboard or config UI**. The human's only job is approving OAuth prompts in a
browser and answering your questions.

Agent ground rules for this walkthrough:

- **Detect and skip.** Every step starts with a check. If it's already done, say so and move on.
- **One account at a time.** Never start a second OAuth flow before the first is confirmed.
- **Print URLs as plain text** so the human can click/copy them from the terminal.
- **Never set `ANTHROPIC_AUTH_TOKEN`** (or `ANTHROPIC_API_KEY`) when wiring Claude Code to
  the LB. Setting it flips Claude Code from subscription ("Claude Max") billing to
  pay-per-token API billing. Base URL only.
- **Ask before editing** the human's `~/.zshrc` or `~/.codex/config.toml`. Show the exact
  block you intend to add.

## 0. Detect existing state

```bash
curl -fsS http://127.0.0.1:2455/health   # already running? skip to step 4
ls ~/Library/LaunchAgents/com.aneyman.agent-lb.plist 2>/dev/null
```

If the server is healthy, jump to **step 4 (connect accounts)** and only revisit earlier
steps if something is missing.

## 1. Prerequisites

- macOS with `git` (Xcode CLT). Python 3.13 is handled by `uv` — no system Python needed.
- `uv`:

```bash
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh
```

(`brew install uv` also works.) `bun` is only needed for the optional dashboard build in
step 6 — skip it by default.

## 2. Clone and install

```bash
git clone https://github.com/aneym/agent-lb.git ~/repos/agent-lb
cd ~/repos/agent-lb
uv sync
```

`uv sync` creates `.venv` with the `agent-lb` and `agent-lb-db` console scripts. The install
is editable — pulling new commits takes effect on the next service restart.

## 3. Run as a service

```bash
scripts/install-service.sh
```

This installs a `com.aneyman.agent-lb` launchd LaunchAgent (KeepAlive, starts at login, logs to
`~/.agent-lb/agent-lb.{out,err}.log`), starts it, and waits for `/health`. Re-run the same
script after pulling updates to restart. `--uninstall` removes it; `--print` shows the plist
without installing.

First start is self-bootstrapping: a SQLite database is created at `~/.agent-lb/store.db`
and migrations run automatically. The server binds `127.0.0.1:2455` only. On a fresh install
no dashboard password exists, so localhost API calls (everything below) work immediately.

Verify:

```bash
curl -fsS http://127.0.0.1:2455/health
```

## 4. Connect accounts — one at a time

Ask the human which accounts they want to pool (Claude and/or ChatGPT, and how many of
each). Then connect them **one at a time**. If they're connecting multiple accounts of the
same provider, tell them to use a separate browser profile or a private window per account
so they approve from the right login.

### Claude (Anthropic) — manual code paste-back

```bash
scripts/anthropic-auth.sh start
```

1. Print the returned auth URL as plain text and note the `flowId`.
2. Tell the human: open the URL in a browser **logged into the Claude account being
   connected**, approve, and paste back the `code#state` string Claude shows them.
3. Wait for the paste, then immediately (codes expire quickly):

```bash
scripts/anthropic-auth.sh complete <flowId> '<code#state>'
```

4. Confirm with `scripts/anthropic-auth.sh accounts` — the account should be `active`.
5. Repeat from `start` for the next Claude account.

### ChatGPT (OpenAI) — automatic browser callback

```bash
scripts/openai-auth.sh start
```

1. Print the returned auth URL and `flowId`. The flow completes **automatically** when the
   browser redirect hits `localhost:1455` — no code to paste. (This requires the browser to
   be on the same machine as the server.)
2. Tell the human to open the URL in a browser logged into the target ChatGPT account and
   approve.
3. Poll `scripts/openai-auth.sh status <flowId>` until `success`, then confirm with
   `scripts/openai-auth.sh accounts`.
4. Repeat for the next account.

If `start` returns `{"method": "browser"}` with no `flowId`, the server treated the
request as already satisfied — check `accounts` before assuming anything failed.

## 5. Wire up clients

### Claude Code

Recommended: install the vendored launch profiles, canonical routing adapters, and
CCDEX worker transport. The installer is previewable, preserves pre-existing regular
client and hook files as `.pre-agent-lb`, checkpoints changed global configuration
under `~/.agent-lb/config-checkpoints/coding-agents/`, and registers the user-scoped
worker MCP:

```bash
scripts/install-claude-clients.sh --print
scripts/install-claude-clients.sh
```

`cc` defaults normal Claude Code to Fable/high. `ccdex` forces the canonical
GPT/high compatibility profile. Normal Claude Code can dispatch GPT workers through
the registered `ccdex-worker` MCP. The installer replaces only known legacy routing
sections, the Claude model field, and equivalent CCDEX hook registrations; unrelated
Claude/Codex instructions, settings, permissions, and hooks are preserved.

Minimal alternative (no launcher):

```zsh
alias cc='ANTHROPIC_BASE_URL=http://127.0.0.1:2455 claude'
```

Either way: **base URL only — never set `ANTHROPIC_AUTH_TOKEN`** (see ground rules). The
launcher strips it defensively and falls back to plain `claude` if the LB is down.

### Anthropic-compatible SDKs

Point Messages API SDK code at `http://127.0.0.1:2455` (root, not `/v1`).
Keep LB credentials in `AGENT_LB_API_KEY` and pass them explicitly from
server-side code or a trusted local script. For the Anthropic Python SDK, use
`auth_token=os.environ.get("AGENT_LB_API_KEY", "sk-local")` so the SDK sends
`Authorization: Bearer ...`; do not export `ANTHROPIC_AUTH_TOKEN` or
`ANTHROPIC_API_KEY` as Agent LB placeholders.

Browser-direct code and deployed loopback URLs cannot reach or spend local
subscription accounts through the LB. Use a reachable Agent LB URL for deployed
server-side integrations.

### Codex CLI

In `~/.codex/config.toml`:

```toml
model_provider = "agent-lb"

[model_providers.agent-lb]
name = "openai"  # required, lowercase
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
supports_websockets = true
requires_openai_auth = true
```

If the human has existing Codex sessions to migrate:
`agent-lb codex-sessions retag --from openai --to agent-lb --dry-run` (then `--yes`).

### AgentLB menu bar app (optional, macOS)

A native status-item client showing pool %, per-account health, and recent
requests. Requires a recent macOS + Xcode CLT (Swift toolchain):

```bash
cd clients/macos-menubar && make install
```

`make install` builds the bundle and registers a
`com.aneyman.agentlb.menubar` LaunchAgent (starts at login). The app enforces
a single instance per machine — a duplicate launch exits silently, which is
expected. To update it later: `git pull`, `make bundle`, then
`launchctl kickstart -k gui/$(id -u)/com.aneyman.agentlb.menubar` (if that
reports `spawn failed`, run a plain `launchctl kickstart` again a few seconds
later — the codesign replace can race the first spawn). Base URL defaults to
`http://127.0.0.1:2455`; for a remote LB set
`defaults write com.aneyman.agentlb.menubar baseURL <url>`.

### Anything OpenAI-compatible (OpenCode, OpenClaw, SDKs)

Point it at `http://127.0.0.1:2455/v1`.

For Vercel AI SDK, OpenAI SDK, and other app integrations, set that base URL
from server-side code. If the app is deployed somewhere else, `127.0.0.1:2455`
points at that remote runtime instead of the user's Mac; use a reachable Agent
LB URL instead. Browser-direct code cannot reach or spend local subscription
accounts through the LB.

## 6. Optional: build the dashboard

The web dashboard is **not** required — everything above is API/CLI-driven, and a fresh
clone ships without the built frontend. If the human wants it:

```bash
cd frontend && bun install && bun run build   # outputs to app/static/
```

Then `http://127.0.0.1:2455` serves it after a service restart.

## 7. Final verification

1. `scripts/anthropic-auth.sh accounts` / `scripts/openai-auth.sh accounts` — all expected
   accounts `active`.
2. Probe each account with a real minimal upstream request (proves auth AND
   subscription, not just stored tokens):

```bash
curl -s http://127.0.0.1:2455/api/accounts | \
  uv run python -c 'import json,sys; [print(a["accountId"], a["provider"], a["email"]) for a in json.load(sys.stdin)["accounts"]]'
curl -s -X POST http://127.0.0.1:2455/api/accounts/<accountId>/probe \
  -H 'content-type: application/json' -d '{}'
```

`probeStatusCode: 200` = usable. `403` with "OAuth authentication is currently
not allowed for this organization" = the org's subscription lapsed (billing
problem — reauth will NOT fix it). `401` = credentials broken (reauth via the
step-4 scripts). See "Account health model" below.

3. A real request through the LB, e.g.:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:2455 claude -p 'reply with OK'
```

For OpenAI-compatible `/v1` clients (OpenCode, OpenClaw, SDKs), discover a live
model first and reuse it:

```bash
MODEL_ID="$(curl -fsS http://127.0.0.1:2455/v1/models | uv run python -c 'import json, sys; print(json.load(sys.stdin)["data"][0]["id"])')"
curl -fsS http://127.0.0.1:2455/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL_ID}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with OK\"}]}"
```

and/or a Codex one-shot if Codex is configured.

4. Report a short status summary to the human: service state, accounts connected, which
   clients are wired, log location.

## Account health model

Every stored account is in exactly one of three states, and they need
different fixes. Diagnose with a probe (never guess from `status` alone):

| State                       | Stored as                                                           | Probe result                                                              | Fix                                                                                                     |
| --------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| **Usable**                  | `status=active`, subscription not `canceled`                        | 2xx                                                                       | none                                                                                                    |
| **Auth'd but unsubscribed** | `status=active` + `subscription.status=canceled`                    | 403 "OAuth authentication is currently not allowed for this organization" | Human resubscribes at the provider's billing page (claude.ai/settings/billing). Reauth will NOT fix it. |
| **Disconnected**            | `status=reauth_required` or `deactivated` (deactivation reason set) | 401 / token refresh fails                                                 | Reauth with the step-4 auth scripts (same flow as adding).                                              |

A background **account pulse** (default on, every 6h, `ACCOUNT_PULSE_*` env
settings) probes every non-paused account and keeps these states truthful
automatically: it detects subscription lapses on idle accounts, marks
credential rejections `reauth_required`, and — after the human resubscribes
or reauths — restores the account to the routing pool within one cycle with
no further action. Transitions are recorded as `account_pulse_*` audit
actions. Unsubscribed and disconnected accounts stay visible in the account
list but are excluded from routing, warmup, and headline counts.

If a projections surface reports elevated `riskLevel`, it is pool-level
(mean across accounts); the `worstAccountEmail`/`worstRiskLevel` fields in
`/api/dashboard/projections` identify the single account driving it, and
`depletion*ByProvider` gives per-provider risk.

## Ongoing operations

For account-specific operations after setup — quota reset checks, stuck or
rate-limited account triage, pause/reactivate routing, billing/subscription
changes, or browser-profile work — use the `agent-lb-account-operator` skill
and the local `.agent-lb/account-profiles.json` registry. Keep one provider
account per dedicated browser profile and do not store secrets there.

Everything is API/CLI-driven — no task below ever requires the dashboard:

| Task                                    | Command                                                                        |
| --------------------------------------- | ------------------------------------------------------------------------------ |
| Restart service (e.g. after `git pull`) | `scripts/install-service.sh`                                                   |
| Stop/remove service                     | `scripts/install-service.sh --uninstall`                                       |
| Logs                                    | `~/.agent-lb/agent-lb.err.log`                                                 |
| Add / reauth an account                 | step 4 scripts                                                                 |
| List accounts                           | `scripts/anthropic-auth.sh accounts`, `scripts/openai-auth.sh accounts`        |
| Verify an account really works          | `POST /api/accounts/<id>/probe` (real upstream request; camelCase response)    |
| Re-check a canceled subscription        | `POST /api/accounts/<id>/subscription/check` (flips ledger back on success)    |
| Pause / reactivate an account           | `POST /api/accounts/<id>/pause` (or `/reactivate`)                             |
| Pool risk / projections                 | `GET /api/dashboard/projections` (pool-level risk + worst-account attribution) |
| Recent traffic per account              | `GET /api/request-logs?accountId=<id>&limit=20`                                |
| Usage summary                           | `GET /api/usage/summary`                                                       |
