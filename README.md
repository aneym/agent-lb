<!--
About
ChatGPT and Claude account load balancer & proxy with usage tracking, dashboard, and OpenAI/Anthropic-compatible endpoints

Topics
python oauth sqlalchemy dashboard load-balancer openai anthropic claude rate-limit api-proxy codex fastapi usage-tracking chatgpt opencode openclaw

Resources
Homepage https://github.com/aneym/agent-lb
Repository https://github.com/aneym/agent-lb
Issues https://github.com/aneym/agent-lb/issues
Releases https://github.com/aneym/agent-lb/releases
Discussions https://github.com/aneym/agent-lb/discussions
Security https://github.com/aneym/agent-lb/security/advisories/new
-->

# agent-lb

Load balancer for ChatGPT and Claude accounts. Pool multiple accounts, track usage, manage API keys, view everything in a dashboard.

## 🤖 Agents look here

**Humans:** don't read further — paste this into your coding agent (Claude Code, Codex
CLI, Cursor, …) and let it drive. **Agents:** your entrypoints are
[GETTING-STARTED.md](GETTING-STARTED.md) (setup runbook) and [AGENTS.md](AGENTS.md)
(working on the codebase); setup and operations never require the dashboard.

```text
Clone https://github.com/aneym/agent-lb into ~/repos/agent-lb, cd into it, and read
GETTING-STARTED.md. Follow it top to bottom to set me up:

1. Install dependencies and start the local agent-lb service.
2. Walk me through connecting my Claude/ChatGPT accounts ONE AT A TIME — for each
   account give me the browser URL, wait for me to approve (and paste back the code
   where required), confirm it connected, then move to the next.
3. When wiring Claude Code, never set ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY;
   use the agent-lb base URL or launcher only so subscription billing stays intact.
4. Wire up my CLI clients (show exact dotfile edits and ask before applying them).
5. Finish by probing every account with a real request and telling me each account's
   state per the "Account health model" (usable / unsubscribed / disconnected),
   plus a short status summary of service, accounts, and wired clients.

If anything is already installed or running, detect it and skip ahead.
```

The full walkthrough lives in [GETTING-STARTED.md](GETTING-STARTED.md). If your agent is
Claude Code or Codex launched inside the repo, saying **"get started"** triggers the
`get-started` skill, which follows the same runbook.

For post-setup account-specific work — quota reset checks, stuck or rate-limited
account triage, billing/subscription changes, pause/reactivate routing,
verification, or browser-profile work — say **"account operator"**. Repo-aware
agents should use the `agent-lb-account-operator` skill and local
`.agent-lb/account-profiles.json` registry for that work.

| ![dashboard](https://github.com/aneym/agent-lb/raw/0cf2d88377b46f4f94d4d04675b2d3ef34d71680/docs/screenshots/dashboard.jpg) | ![accounts](https://github.com/aneym/agent-lb/raw/0cf2d88377b46f4f94d4d04675b2d3ef34d71680/docs/screenshots/accounts.jpg) |
| :------------------------------------------: | :----------------------------------------: |

<details>
<summary>More screenshots</summary>

|                  Settings                  |                Login                 |
| :----------------------------------------: | :----------------------------------: |
| ![settings](https://github.com/aneym/agent-lb/raw/0cf2d88377b46f4f94d4d04675b2d3ef34d71680/docs/screenshots/settings.jpg) | ![login](https://github.com/aneym/agent-lb/raw/0cf2d88377b46f4f94d4d04675b2d3ef34d71680/docs/screenshots/login.jpg) |

|                    Dashboard (dark)                    |                   Accounts (dark)                    |                   Settings (dark)                    |
| :----------------------------------------------------: | :--------------------------------------------------: | :--------------------------------------------------: |
| ![dashboard-dark](https://github.com/aneym/agent-lb/raw/0cf2d88377b46f4f94d4d04675b2d3ef34d71680/docs/screenshots/dashboard-dark.jpg) | ![accounts-dark](https://github.com/aneym/agent-lb/raw/0cf2d88377b46f4f94d4d04675b2d3ef34d71680/docs/screenshots/accounts-dark.jpg) | ![settings-dark](https://github.com/aneym/agent-lb/raw/0cf2d88377b46f4f94d4d04675b2d3ef34d71680/docs/screenshots/settings-dark.jpg) |

</details>

## Features

<table>
<tr>
<td><b>Account Pooling</b><br>Load balance across multiple ChatGPT and Claude accounts</td>
<td><b>Usage Tracking</b><br>Per-account tokens, cost, 28-day trends</td>
<td><b>API Keys</b><br>Per-key rate limits by token, cost, window, model</td>
</tr>
<tr>
<td><b>Dashboard Auth</b><br>Password + optional TOTP</td>
<td><b>Client-compatible</b><br>Codex CLI, Claude Code, OpenCode, OpenClaw, and SDKs</td>
<td><b>Auto Model Sync</b><br>Available models fetched from upstream</td>
</tr>
</table>

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

## Quick Start

### Source checkout

This always works, including before PyPI/GHCR artifacts for a prerelease have
been published:

```bash
git clone https://github.com/aneym/agent-lb.git
cd agent-lb
uv sync
scripts/install-service.sh
```

Source checkout starts the API/service. A git clone does not include built
dashboard assets; connect accounts from the CLI with
[`GETTING-STARTED.md` step 4](GETTING-STARTED.md#4-connect-accounts--one-at-a-time):

```bash
scripts/anthropic-auth.sh start
scripts/openai-auth.sh start
```

To use the dashboard from a source checkout, build the frontend and restart the
service:

```bash
cd frontend && bun install && bun run build
cd ..
scripts/install-service.sh
```

## Remote Setup

When accessing the dashboard remotely for the first time, a bootstrap token is required to set the initial password.

**Auto-generated (default):** On first startup (no password configured), the server generates a one-time token and prints it to logs:

```bash
docker logs agent-lb
# ============================================
#   Dashboard bootstrap token (first-run):
#   <token>
# ============================================
```

Open the dashboard → enter the token + new password → done. The token is shared across replicas and remains valid until a password is set. In multi-replica setups, replicas must share the same encryption key (the Helm chart default) for restart recovery to work.

**Manual token:** To use a fixed token instead, set the env var before starting:

```bash
docker run -d --name agent-lb \
  -e AGENT_LB_DASHBOARD_BOOTSTRAP_TOKEN=your-secret-token \
  -p 2455:2455 -p 1455:1455 \
  -v agent-lb-data:/var/lib/agent-lb \
  ghcr.io/aneym/agent-lb:1.20.0-beta.3
```

**Local access** (localhost) bypasses bootstrap entirely — no token needed.

## Client Setup

[Client setup](CLIENT-SETUP.md) covers Codex CLI, Claude Code, SDKs, OpenCode, and OpenClaw.

## macOS Menu Bar App

A native macOS 26 menu bar companion (`clients/macos-menubar/`) puts the
dashboard's vitals one click away: a status-bar ring gauge that drains with
the pool's 5-hour window, separated 5-hour / weekly limit cards with
`next reset in … · +n cr` recovery, a provider scope control (All / Codex /
Claude) that filters every stat exactly like the dashboard, an account list
with status/sort/search filtering and pause/reactivate, recent requests, and
service start/restart/stop via launchd.

```bash
cd clients/macos-menubar
make install   # build, bundle, and register as a login LaunchAgent (starts now + at every login)
```

`make install` is the supported way to run it: it copies the bundle to
`~/Applications/AgentLB.app` (internal disk, and the folder Raycast and
Spotlight index, so "AgentLB" opens from either) and registers that copy in
the GUI launchd domain, which makes startup automatic at login. The
LaunchAgent waits for ControlCenter before starting the app: launched too
early at login, or with `open` from an SSH session, the process runs but the
menu bar item never registers on macOS 26. As a second guard the app checks
for its own status item 15 s and 45 s after launch and exits 3 when it is
missing, and launchd (`KeepAlive` on non-zero exit) relaunches it; a counter
in `~/Library/Application Support/AgentLB/` caps this at five consecutive
relaunches. If the icon ever vanishes:

```bash
launchctl kickstart -k gui/$(id -u)/com.aneyman.agentlb.menubar
```

`make uninstall` removes the agent; `make test` runs the unit suite.

**Remote machines (Tailscale):** copy `AgentLB.app` anywhere (e.g.
`~/Applications`), point it at the service host, and register the agent for
the copied path:

```bash
defaults write com.aneyman.agentlb.menubar baseURL "https://<host>.<tailnet>.ts.net:2455"
make install-agent APP_PATH=$HOME/Applications/AgentLB.app
```

When the base URL isn't local the app runs in remote mode: the header shows
the host and the launchd service controls hide. If your menu bar is full
(notched MacBooks hide overflow items), nudge the icon right of the notch:

```bash
defaults write com.aneyman.agentlb.menubar "NSStatusItem Preferred Position Item-0" -float 220
```

## API Key Authentication

API key auth is **disabled by default**. In that mode, only local requests to the protected proxy routes can
proceed without a key; non-local requests are rejected until proxy authentication is configured. Enable it in
**Settings → API Key Auth** on the dashboard when clients connect remotely or through Docker, VM, or container
networking that appears non-local to the service.

When enabled, clients must pass a valid API key as a Bearer token:

```
Authorization: Bearer sk-clb-...
```

The protected proxy routes covered by this setting are:

- `/v1/*` (except `/v1/usage`, which always requires a valid key)
- `/backend-api/codex/*`
- `/backend-api/transcribe`

**Creating keys**: Dashboard → API Keys → Create. The full key is shown **only once** at creation. Keys support optional expiration, model restrictions, and rate limits (tokens / cost per day / week / month).

## Configuration

Environment variables with `AGENT_LB_` prefix or `.env.local`. See [`.env.example`](.env.example).
SQLite is the default database backend; PostgreSQL is optional via `AGENT_LB_DATABASE_URL` (for example `postgresql+asyncpg://...`).

The Docker Compose `postgres` profile uses the Postgres 18 image and mounts the named data volume at
`/var/lib/postgresql`, the parent of the image's versioned `PGDATA` directory.

Existing Postgres 16 compose volumes must be upgraded before the Postgres 18 container starts:

```bash
docker compose --profile postgres stop postgres
docker run --rm -v agent-lb-postgres-data:/var/lib/postgresql -v "$PWD:/backup" alpine \
  tar -C /var/lib/postgresql -czf /backup/agent-lb-postgres-data-before-pg18.tgz .
docker compose --profile postgres-upgrade run --rm postgres-upgrade
docker compose --profile postgres up -d postgres
```

The `postgres-upgrade` profile runs `pg_upgrade` in one-shot mode against the same named volume and exits after the
data directory has been upgraded to the Postgres 18 layout. Because that helper mounts and rewrites the operator's
database volume, Compose pins the helper image by digest; refresh and review the digest deliberately when changing the
helper image tag. Keep the backup until the application has started and `agent-lb-db check` succeeds against the
upgraded database.

The normal `postgres` service refuses to start when it detects the old root-level `PG_VERSION` file from a pre-18
Compose volume. If that guard fires, run the `postgres-upgrade` profile above before starting Postgres again.
It also refuses nested `/var/lib/postgresql/data` directories that still report a pre-18 major version, because those
layouts need an explicit pg_upgrade before the Postgres 18 container can safely open them.

### Dashboard authentication modes

`agent-lb` supports three dashboard auth modes via environment variables:

- `AGENT_LB_DASHBOARD_AUTH_MODE=standard` — built-in dashboard password with optional TOTP from the Settings page.
- `AGENT_LB_DASHBOARD_AUTH_MODE=trusted_header` — trust a reverse-proxy auth header such as Authelia's `Remote-User`, but only from `AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS`. Built-in password/TOTP remain available as an optional fallback, and password/TOTP management still requires a fallback password session.
- `AGENT_LB_DASHBOARD_AUTH_MODE=disabled` — fully bypass dashboard auth. Use only behind network restrictions or external auth. Built-in password/TOTP management is disabled in this mode.

`trusted_header` mode also requires:

```bash
AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS=true
AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS=172.18.0.0/16
AGENT_LB_DASHBOARD_AUTH_PROXY_HEADER=Remote-User
```

If the trusted header is missing and no fallback password is configured, the dashboard fails closed and shows a reverse-proxy-required message instead of loading the UI.

### Docker examples

**Authelia / trusted header**

```bash
docker run -d --name agent-lb \
  -p 2455:2455 -p 1455:1455 \
  -e AGENT_LB_DASHBOARD_AUTH_MODE=trusted_header \
  -e AGENT_LB_DASHBOARD_AUTH_PROXY_HEADER=Remote-User \
  -e AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS=true \
  -e AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS=172.18.0.0/16 \
  -v agent-lb-data:/var/lib/agent-lb \
  ghcr.io/aneym/agent-lb:1.20.0-beta.3
```

**Hard override / no app-level dashboard auth**

```bash
docker run -d --name agent-lb \
  -p 2455:2455 -p 1455:1455 \
  -e AGENT_LB_DASHBOARD_AUTH_MODE=disabled \
  -v agent-lb-data:/var/lib/agent-lb \
  ghcr.io/aneym/agent-lb:1.20.0-beta.3
```

For Helm, pass the same values through `extraEnv`.

## Data

| Environment | Path                 |
| ----------- | -------------------- |
| Local / uvx | `~/.agent-lb/`       |
| Docker      | `/var/lib/agent-lb/` |

Backup this directory to preserve your data.

## Troubleshooting

- [Usage and quota - why does agent-lb still say `rate_limited` when Codex Desktop says reset?](openspec/specs/usage-refresh-policy/context.md)

## Development

```bash
# Docker
docker compose watch

# Local
uv sync && cd frontend && bun install && cd ..
uv run fastapi run app/main.py --reload        # backend :2455
cd frontend && bun run dev                     # frontend :5173
```
