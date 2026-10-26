# Client Setup

Point each client at the matching agent-lb surface. OpenAI-compatible clients use `/v1`, Codex uses `/backend-api/codex`, and Claude Code uses the Anthropic-compatible base URL. Anthropic-compatible SDKs use the same root Messages API surface. If [API key auth](README.md#api-key-authentication) is enabled, pass a key from the dashboard as a Bearer token for HTTP clients that support it.

Use `127.0.0.1` only for clients running on the same machine as agent-lb.
Server-side app integrations such as the Vercel AI SDK or OpenAI SDK can set
their OpenAI-compatible base URL to `/v1`; browser-direct code and deployed
loopback URLs cannot reach or spend a user's local subscription accounts.

Model availability is discovered from the upstream Codex model catalog and can vary by account plan, workspace, rollout, and upstream deprecation state. Prefer the live `GET /v1/models` or `GET /backend-api/codex/models` response over a copied static table when configuring clients or API-key model allowlists.

| Logo                                                                                                              | Client                   | Endpoint                                  | Config                             |
| ----------------------------------------------------------------------------------------------------------------- | ------------------------ | ----------------------------------------- | ---------------------------------- |
| <img src="https://avatars.githubusercontent.com/u/14957082?s=200" width="32" alt="OpenAI">                        | **Codex CLI**            | `http://127.0.0.1:2455/backend-api/codex` | `~/.codex/config.toml`             |
| <img src="https://avatars.githubusercontent.com/u/76263028?s=200" width="32" alt="Anthropic">                     | **Claude Code**          | `http://127.0.0.1:2455`                   | `ANTHROPIC_BASE_URL` / launcher    |
| <img src="https://avatars.githubusercontent.com/u/76263028?s=200" width="32" alt="Anthropic">                     | **Anthropic Python SDK** | `http://127.0.0.1:2455`                   | Code                               |
| <img src="https://avatars.githubusercontent.com/u/208539476?s=200" width="32" alt="OpenCode">                     | **OpenCode**             | `http://127.0.0.1:2455/v1`                | `~/.config/opencode/opencode.json` |
| <img src="https://avatars.githubusercontent.com/u/252820863?s=200" width="32" alt="OpenClaw">                     | **OpenClaw**             | `http://127.0.0.1:2455/v1`                | `~/.openclaw/openclaw.json`        |
| <img src="https://assets.vercel.com/image/upload/front/favicon/vercel/180x180.png" width="32" alt="Vercel">       | **Vercel AI SDK**        | `http://127.0.0.1:2455/v1`                | Server route / action              |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg" width="32" alt="Python"> | **OpenAI Python SDK**    | `http://127.0.0.1:2455/v1`                | Code                               |

<details>
<summary><img src="https://avatars.githubusercontent.com/u/14957082?s=200" width="20" align="center" alt="OpenAI">&ensp;<b>Codex CLI / IDE Extension</b></summary>
<br>

`~/.codex/config.toml`:

```toml
model = "gpt-5.3-codex"
model_reasoning_effort = "xhigh"
model_provider = "agent-lb"

[model_providers.agent-lb]
name = "openai"  # required — enables remote /responses/compact. Lowercase since Codex 2026-05-23; older "OpenAI" stops resolving gpt-5.5
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
supports_websockets = true
requires_openai_auth = true # required for codex app
```

Optional: enable native upstream WebSockets for Codex streaming while keeping `agent-lb` pooling:

```bash
export AGENT_LB_UPSTREAM_STREAM_TRANSPORT=websocket
```

`auto` is the default and uses native WebSockets for native Codex headers or models that prefer them.
You can also switch this in the dashboard under Settings -> Routing -> Upstream stream transport.

Note: Codex itself does not currently expose a stable documented `wire_api = "websocket"` provider mode.
If you want to experiment on the Codex side, the current CLI exposes under-development feature flags:

```toml
[features]
responses_websockets = true
# or
responses_websockets_v2 = true
```

These flags are experimental and do not replace `wire_api = "responses"`.

Upstream websocket handshakes automatically honor standard proxy environment variables when they are
present. `wss://` handshakes check `wss_proxy`, `socks_proxy`, `https_proxy`, and `all_proxy`;
plain `ws://` handshakes also check `ws_proxy` and `http_proxy`. Set
`AGENT_LB_UPSTREAM_WEBSOCKET_TRUST_ENV=false` only when websocket handshakes must bypass those
environment proxies and connect directly.

**With [API key auth](README.md#api-key-authentication):**

```toml
[model_providers.agent-lb]
name = "openai"
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
env_key = "AGENT_LB_API_KEY"
supports_websockets = true
requires_openai_auth = true # required for codex app
```

```bash
export AGENT_LB_API_KEY="sk-clb-..."   # key from dashboard
codex
```

**Verify WebSocket transport**

Use a one-off debug run:

```bash
RUST_LOG=debug codex exec "Reply with OK only."
```

Healthy websocket signals:

- CLI logs contain `connecting to websocket` and `successfully connected to websocket`
- `agent-lb` logs show `WebSocket /backend-api/codex/responses`
- `agent-lb` logs do **not** show fallback `POST /backend-api/codex/responses` for the same run

If you run `agent-lb` behind a reverse proxy, make sure it forwards WebSocket upgrades.

**Usage in T3 Code and `/status` without a ChatGPT login.** Codex reads rate limits
(`account/rateLimits/read`) only under ChatGPT auth. On a host with no login of its own, run
`scripts/codex-pool-identity` once per Codex home: it writes a placeholder login for the
`agent-lb-pool` account and sets `chatgpt_base_url` to the agent-lb root, so Codex reads pooled
usage from `/api/codex/usage`. agent-lb serves the pool view only to clients it already serves
without a key; with API key auth on, a key in `access_token` sees that key's limits. A real login
is left alone. `--check` exits 1 when the identity is missing.

**Migrating from direct OpenAI** — `codex resume` filters by `model_provider`;
old sessions won't appear until you re-tag them. Use the built-in retag command
instead of editing Codex files by hand; see
[Codex session retagging](openspec/specs/runtime-portability/context.md#codex-session-retagging) for backups, Docker, WSL,
and rollback details.

```bash
# Preview what will change first.
agent-lb codex-sessions retag --from openai --to agent-lb --dry-run

# Then close Codex/Codex CLI and apply the retag.
agent-lb codex-sessions retag --from openai --to agent-lb --yes
```

</details>

<details>
<summary><img src="https://avatars.githubusercontent.com/u/76263028?s=200" width="20" align="center" alt="Anthropic">&ensp;<b>Claude Code</b></summary>
<br>

Connect Claude (Anthropic) accounts first — `scripts/anthropic-auth.sh` or step 4 of
[GETTING-STARTED.md](GETTING-STARTED.md). Then either launch with the base URL only:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:2455 claude
```

or install the vendored `cc` launcher and canonical routing policy. Preview the exact
changes first; installation preserves existing regular client/hook files, checkpoints
changed global configuration, and removes retired CCDEX artifacts when present:

```bash
scripts/install-claude-clients.sh --print
scripts/install-claude-clients.sh
```

`cc` defaults to Fable/high. GPT compatibility seats run inside the Claude Code harness
through agent-lb's server-side model aliases. The installer semantically updates only
routing-owned Markdown blocks and the Claude model field; unrelated global
configuration remains intact.

For Claude Desktop's embedded **Code** runtime on macOS, install the dedicated shared
loopback proxy after the main service is healthy:

```bash
scripts/install-claude-desktop-proxy.sh --print
scripts/install-claude-desktop-proxy.sh
```

It runs as the KeepAlive LaunchAgent
`com.aneyman.agent-lb-claude-desktop-proxy`, verifies the proxied Anthropic health
endpoint before atomically updating `~/.claude/settings.json`, and preserves unrelated
settings. Fully quit and reopen Claude Desktop after install. This routes the embedded
Code runtime; ordinary Claude Desktop chat is not a supported or claimed surface.
Within `api.anthropic.com`, only Messages API calls route to agent-lb. OAuth, telemetry,
feature, and Code worker traffic keeps its caller credentials and goes directly to
Anthropic; unknown future paths default direct.

Verify the integration by running a Desktop Code task and correlating it with a fresh
agent-lb request/session record. Logs are written to
`~/.agent-lb/claude-desktop-proxy.err.log`. To remove it and conditionally restore only
installer-owned settings:

```bash
scripts/install-claude-desktop-proxy.sh --uninstall
```

> **Important**: do **not** set `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY` when pointing
> Claude Code at agent-lb. Setting either flips Claude Code from subscription ("Claude Max")
> billing to per-token API billing. The launcher strips both defensively and falls back to
> plain `claude` if the load balancer is down (`CLAUDE_LB_DISABLE=1` forces the bypass).

Sticky routing without the launcher: claim a route via `POST /api/anthropic/session-route`
with `{"sessionId", "model", "quotaKey"}` and send `x-claude-session-id: <sessionId>` on
requests.

</details>

<details>
<summary><img src="https://avatars.githubusercontent.com/u/76263028?s=200" width="20" align="center" alt="Anthropic">&ensp;<b>Anthropic Python SDK</b></summary>
<br>

Install the official Anthropic SDK:

```bash
pip install anthropic
```

Use this from server-side code or a trusted local script:

```python
import os

from anthropic import Anthropic

client = Anthropic(
    base_url=os.environ.get("AGENT_LB_ANTHROPIC_BASE_URL", "http://127.0.0.1:2455"),
    auth_token=os.environ.get("AGENT_LB_API_KEY", "sk-local"),
)

message = client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=64,
    messages=[{"role": "user", "content": "Hello!"}],
)
print(message.content[0].text)
```

`auth_token` makes the SDK send the Agent LB key as `Authorization: Bearer ...`,
which is required when [API key auth](README.md#api-key-authentication) is enabled. Use a
dashboard key in `AGENT_LB_API_KEY`; if auth is disabled and the script runs on
the same machine, any non-empty local placeholder is fine.

Do not export `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY` as Agent LB
placeholders. Those names are first-party Anthropic/Claude credentials; keep LB
credentials in `AGENT_LB_API_KEY` and pass them explicitly in SDK code.

Like the OpenAI-compatible SDK examples, keep this server-side or local.
Browser-direct code and deployed loopback URLs cannot reach or spend local
subscription accounts through the LB.

</details>

<details>
<summary><img src="https://avatars.githubusercontent.com/u/208539476?s=200" width="20" align="center" alt="OpenCode">&ensp;<b>OpenCode</b></summary>
<br>

> **Important**: Use the built-in `openai` provider with `baseURL` override — not a custom provider with `@ai-sdk/openai-compatible`. Custom providers use the Chat Completions API which **drops reasoning/thinking content**. The built-in `openai` provider uses the Responses API, which properly preserves `encrypted_content` and multi-turn reasoning state.

Before starting, make sure all existing OpenAI credentials are cleared from `~/.local/share/opencode/auth.json`.
You can clean the config by using this one-liner
`jq 'del(.openai)' ~/.local/share/opencode/auth.json > auth.json.tmp && mv auth.json.tmp ~/.local/share/opencode/auth.json`

`~/.config/opencode/opencode.json`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "openai": {
      "options": {
        "baseURL": "http://127.0.0.1:2455/v1",
        "apiKey": "{env:AGENT_LB_API_KEY}",
      },
      "models": {
        "gpt-5.4": {
          "name": "GPT-5.4",
          "reasoning": true,
          "options": {
            "reasoningEffort": "high",
            "reasoningSummary": "detailed",
          },
          "limit": { "context": 1050000, "output": 128000 },
        },
        "gpt-5.3-codex": {
          "name": "GPT-5.3 Codex",
          "reasoning": true,
          "options": {
            "reasoningEffort": "high",
            "reasoningSummary": "detailed",
          },
          "limit": { "context": 272000, "output": 65536 },
        },
        "gpt-5.1-codex-mini": {
          "name": "GPT-5.1 Codex Mini",
          "reasoning": true,
          "options": {
            "reasoningEffort": "high",
            "reasoningSummary": "detailed",
          },
          "limit": { "context": 272000, "output": 65536 },
        },
        "gpt-5.3-codex-spark": {
          "name": "GPT-5.3 Codex Spark",
          "reasoning": true,
          "options": {
            "reasoningEffort": "xhigh",
            "reasoningSummary": "detailed",
          },
          "limit": { "context": 128000, "output": 65536 },
        },
      },
    },
  },
  "model": "openai/gpt-5.3-codex",
}
```

This overrides the built-in `openai` provider's endpoint to point at agent-lb while keeping the Responses API code path that handles reasoning properly.

```bash
export AGENT_LB_API_KEY="sk-clb-..."   # key from dashboard
opencode
```

</details>

<details>
<summary><img src="https://avatars.githubusercontent.com/u/252820863?s=200" width="20" align="center" alt="OpenClaw">&ensp;<b>OpenClaw</b></summary>
<br>

`~/.openclaw/openclaw.json`:

```jsonc
{
  "agents": {
    "defaults": {
      "model": { "primary": "agent-lb/gpt-5.4" },
      "models": {
        "agent-lb/gpt-5.4": { "params": { "cacheRetention": "short" } },
        "agent-lb/gpt-5.4-mini": { "params": { "cacheRetention": "short" } },
        "agent-lb/gpt-5.3-codex": { "params": { "cacheRetention": "short" } },
      },
    },
  },
  "models": {
    "mode": "merge",
    "providers": {
      "agent-lb": {
        "baseUrl": "http://127.0.0.1:2455/v1",
        "apiKey": "${AGENT_LB_API_KEY}", // or "dummy" if API key auth is disabled
        "api": "openai-responses",
        "models": [
          {
            "id": "gpt-5.4",
            "name": "gpt-5.4 (agent-lb)",
            "contextWindow": 1050000,
            "contextTokens": 272000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false,
          },
          {
            "id": "gpt-5.4-mini",
            "name": "gpt-5.4-mini (agent-lb)",
            "contextWindow": 400000,
            "contextTokens": 272000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false,
          },
          {
            "id": "gpt-5.3-codex",
            "name": "gpt-5.3-codex (agent-lb)",
            "contextWindow": 400000,
            "contextTokens": 272000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false,
          },
        ],
      },
    },
  },
}
```

Set the env var or replace `${AGENT_LB_API_KEY}` with a key from the dashboard. If API key auth is disabled,
local requests can omit the key, but non-local requests are still rejected until proxy authentication is configured.

The `/v1` route is the simplest OpenAI-compatible setup. If your OpenClaw build uses a Codex-native provider path such as `openai-codex-responses` and needs Codex-style usage/accounting behavior, point that provider at `http://127.0.0.1:2455/backend-api/codex` instead. For third-party Codex-compatible backends, the client must allow opaque bearer-token passthrough and should only send `chatgpt-account-id` when it actually decoded one from an official ChatGPT/Codex token.

</details>

<details>
<summary><img src="https://assets.vercel.com/image/upload/front/favicon/vercel/180x180.png" width="20" align="center" alt="Vercel">&ensp;<b>Vercel AI SDK</b></summary>
<br>

Install the OpenAI provider in the app:

```bash
pnpm add ai @ai-sdk/openai
```

Use this only from server-side code, such as a route handler or server action:

```ts
import { createOpenAI } from "@ai-sdk/openai";
import { generateText } from "ai";

const agentLB = createOpenAI({
  baseURL: process.env.AGENT_LB_BASE_URL ?? "http://127.0.0.1:2455/v1",
  apiKey: process.env.AGENT_LB_API_KEY ?? "sk-local",
});

export async function POST(req: Request) {
  const { prompt } = await req.json();
  const { text } = await generateText({
    model: agentLB.responses("gpt-5.3-codex"),
    prompt,
  });

  return Response.json({ text });
}
```

`127.0.0.1:2455` works only when that server route runs on the same machine as
agent-lb. Deployed apps need `AGENT_LB_BASE_URL` set to a reachable Agent LB URL
and should not route through Vercel AI Gateway if the goal is to spend local
subscription accounts through this proxy.

</details>

<details>
<summary><img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg" width="20" align="center" alt="Python">&ensp;<b>OpenAI Python SDK</b></summary>
<br>

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:2455/v1",
    api_key="sk-clb-...",  # from dashboard, or any non-empty string if auth is disabled
)

response = client.chat.completions.create(
    model="gpt-5.3-codex",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

</details>
