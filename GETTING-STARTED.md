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

## Check quota without making a model call

```bash
agent-lb status
agent-lb status --provider anthropic --model claude-fable-5-1 --thinking --json
```

These commands read the running service; they do not start a server, make inference
calls, or block work. Use the reported windows, resets, and model-specific reasons
to plan work. Low headroom is information, not permission to deny a dispatch.
A successful status read exits 0 even when quota is exhausted; service or telemetry
errors exit 2. Unknown or stale telemetry is not proof that an account is exhausted.
The snapshot is not a reservation or a guarantee that the next request will succeed.
Actual provider limits, authentication, and explicitly configured spending limits
still apply. Set `AGENT_LB_BASE_URL` or pass `--base-url` for a non-default service.
From a checkout without a PATH install, use `.venv/bin/agent-lb status`.
For an existing internal runtime, `scripts/install-status-cli.sh` adds the PATH
entry under `~/.local/bin` without restarting the service or replacing a command.

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

## Join an existing pool (federation follower)

Use this alternative to step 4 when the machine should consume accounts from an existing
agent-lb owner. The Mac must be on the same tailnet as the owner; clone the repository, run
`uv sync`, and install the service through step 3 first. Followers do **not** connect OAuth
accounts locally—the mirror imports routable access tokens while refresh ownership remains
with the pool owner.

Get the shared federation token from the pool operator without putting it on the command
line, then configure and verify the follower:

```bash
export AGENT_LB_FEDERATION_TOKEN='<shared-token>'
scripts/install-federation-follower.sh \
  --peer-url https://studio.tailnet.example:2455 \
  --instance-id partner-mba
./clients/agent-lb-federation status
```

The installer exits successfully only after the local status endpoint observes a completed
mirror pull. After that, continue to step 5 to wire clients normally. Use `--print` to preview
the four federation environment values with the token redacted, or `--uninstall` to remove
only those federation values and restart the service.

## 4. Connect accounts — one at a time

Ask the human which accounts they want to pool (Claude and/or ChatGPT, and how many of
each). Then connect them **one at a time**. If they're connecting multiple accounts of the
same provider, tell them to use a separate browser profile or a private window per account
so they approve from the right login.

After adding an account with a paid plan, record it with `agent-lb subscriptions set <email> --plan <plan> --renews-on YYYY-MM-DD --payment card|single-use|none`.

### Claude (Anthropic) — registered browser profile

For an agent operating on Studio, use the
[account enrollment workflow](.agents/skills/agent-lb-account-operator/SKILL.md#enroll-a-subscription-account).
It includes purchasing a plan when requested, phone verification, enrollment and
live readiness checks. Do not ask for an OAuth code when CDP can collect the callback.

```bash
agent-lb profiles list --json
agent-lb profiles add EMAIL --provider anthropic
agent-lb profiles open EMAIL
agent-lb reauth EMAIL --provider anthropic --timeout 180 --json
```

Skip `profiles add` when already registered. The registered directory may differ
from the email; `reauth --dry-run` shows the exact path and port. Verify the signed-in
website email, authorize Claude Code, then let `reauth` collect the callback.
Check the new account with `POST /api/accounts/{accountId}/probe`; `active` alone
only confirms stored authorization, not working upstream access.

When browser automation is unavailable, `scripts/anthropic-auth.sh start` and
`complete <flowId> '<code#state>'` remain the manual fallback. Never print or save
credentials or callback codes in reports.

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

`start` forces a browser OAuth flow and returns an `authorizationUrl` and `flowId`,
even when an OpenAI account is already in the pool. Use the same command to reauthenticate
an existing account.

### Kimi Coding Plan — API key

1. Create a key in the Kimi Code console (starts `sk-kimi-`; Moonshot platform keys fail).
2. Dashboard: Providers, Add account, Kimi, paste the key. Each key becomes its own
   account; pasting the same key again updates it. Or, with the key in `$KIMI_KEY`:

   ```bash
   jq -n --arg k "$KIMI_KEY" '{provider:"kimi",apiKey:$k}' | curl -s -X POST \
     http://127.0.0.1:2455/api/accounts/import/api-key -H 'content-type: application/json' -d @-
   ```

3. Probe it: `POST /api/accounts/{accountId}/probe` must return `probeStatusCode: 200`
   (model `kimi-for-coding`). A 401 for a known-good key means the other region: set `AGENT_LB_KIMI_ANTHROPIC_UPSTREAM_BASE_URL` to
   `https://api.kimi.ai/coding` (overseas) or `https://api.kimi.com/coding` (default) and
   restart with `lb-restart`, then `POST /api/accounts/{accountId}/reactivate`.
4. Serve: send `kimi-for-coding` (every plan), `k3`, `k3-256k` or `k3.1-pro` (higher plans) to
   `/v1/messages` or `/v1/chat/completions`. A plan that lacks a model returns 403 to the
   caller without benching the account.

A kimi-cli login can be imported instead with `{"provider":"kimi","apiKey":<access_token>,
"refreshToken":<refresh_token>}` from `~/.kimi/credentials/kimi-code.json`; stop using that
kimi-cli login afterwards, because both sides rotate the same refresh token.

## 5. Wire up clients

### Claude Code

Recommended: install the vendored `cc` launcher and canonical routing policy. The
installer is previewable, preserves pre-existing regular client and hook files as
`.pre-agent-lb`, checkpoints changed global configuration under
`~/.agent-lb/config-checkpoints/coding-agents/`, and removes retired CCDEX clients,
hooks, and MCP registration when present:

```bash
scripts/install-claude-clients.sh --print
scripts/install-claude-clients.sh
```

`cc` defaults normal Claude Code to Fable/high. GPT compatibility seats run inside
the Claude Code harness through agent-lb's server-side model aliases. The installer
replaces only routing-owned policy sections and the Claude model field; unrelated
Claude/Codex instructions, settings, permissions, and hooks are preserved.

### Claude Desktop Code (macOS)

Claude Desktop's embedded Code runtime does not start through the `cc` wrapper. Install
the dedicated loopback proxy after the main service and Claude accounts are healthy:

```bash
scripts/install-claude-desktop-proxy.sh --print
scripts/install-claude-desktop-proxy.sh
```

The installer creates the KeepAlive LaunchAgent
`com.aneyman.agent-lb-claude-desktop-proxy`, verifies a real Anthropic health request
through `127.0.0.1:2458`, and only then updates `~/.claude/settings.json`. It preserves
unrelated settings, refuses conflicting proxy or CA values, and keeps its private CA
under `~/.agent-lb/tls/` rather than the system trust store. Fully quit and reopen
Claude Desktop after installation, then run a task from its **Code** surface.
The proxy sends only Messages API calls to agent-lb. Same-host OAuth, telemetry,
feature, and Code worker requests go directly to Anthropic, and unknown future paths
default direct rather than entering the local service.

This integration is specifically for Claude Desktop's embedded Code runtime. It does
not claim to route ordinary Claude Desktop chat. Verify Code traffic with a fresh
agent-lb request/session record; a successful `claude -p` CLI request alone is not a
Desktop end-to-end test.

Rollback is explicit and conditional, so later operator edits are preserved:

```bash
scripts/install-claude-desktop-proxy.sh --uninstall
```

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
step-4 scripts). See [Account health](docs/account-health.md).

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

## After setup

Account states and day-to-day commands are in [docs/account-health.md](docs/account-health.md).
