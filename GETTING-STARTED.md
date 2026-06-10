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
ls ~/Library/LaunchAgents/com.agent-lb.plist 2>/dev/null
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

This installs a `com.agent-lb` launchd LaunchAgent (KeepAlive, starts at login, logs to
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

Recommended: the vendored launcher, which adds sticky per-session account routing (keeps
prompt cache on one account) and a quota banner:

```zsh
# in ~/.zshrc — adjust the path to the clone location
cc() { "$HOME/repos/agent-lb/clients/claude-lb-launch" "$@"; }
```

Minimal alternative (no launcher):

```zsh
alias cc='ANTHROPIC_BASE_URL=http://127.0.0.1:2455 claude'
```

Either way: **base URL only — never set `ANTHROPIC_AUTH_TOKEN`** (see ground rules). The
launcher strips it defensively and falls back to plain `claude` if the LB is down.

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

### Anything OpenAI-compatible (OpenCode, SDKs)

Point it at `http://127.0.0.1:2455/v1`.

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
2. A real request through the LB, e.g.:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:2455 claude -p 'reply with OK'
```

and/or a Codex one-shot if Codex is configured.

3. Report a short status summary to the human: service state, accounts connected, which
   clients are wired, log location.

## Ongoing operations

| Task                                    | Command                                                                         |
| --------------------------------------- | ------------------------------------------------------------------------------- |
| Restart service (e.g. after `git pull`) | `scripts/install-service.sh`                                                    |
| Stop/remove service                     | `scripts/install-service.sh --uninstall`                                        |
| Logs                                    | `~/.agent-lb/agent-lb.err.log`                                                  |
| Add another account                     | step 4 scripts                                                                  |
| Pause / reactivate an account           | `curl -X POST http://127.0.0.1:2455/api/accounts/<id>/pause` (or `/reactivate`) |
| List accounts                           | `scripts/anthropic-auth.sh accounts`, `scripts/openai-auth.sh accounts`         |
