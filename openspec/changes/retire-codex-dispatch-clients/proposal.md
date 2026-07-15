# Retire the Codex dispatch clients and dual-mode routing policy

## Why

The machine owner consolidated coding-agent model routing into a single canon
(2026-07-15): Claude Code is the only coding harness, and per-subagent model
choice will move into an agent-lb model-alias registry (planned separately).
The Codex dispatch layer — the `ccdex` entry point, the `ccdex-worker` MCP
transport, the `ccdex-gpt-only` guard hook, and the codex-host instruction
adapter — is retired. Keeping the installer and policy shipping those artifacts
would resurrect the retired stack on every install.

The server-side CCDEX compatibility bridge (`/v1/ccdex/messages` translation,
locked Sol profile, overflow handling, reasoning hygiene, token counting) is
NOT retired: it is the substrate for the planned alias-registry bridge and its
requirements remain in force. `clients/claude-lb-launch` keeps its codex-mode
internals dormant until the alias-registry change lands; no launcher behavior
changes here.

## What Changes

- Delete `clients/ccdex`, `clients/ccdex-worker-mcp`,
  `config/coding-agents/ccdex-gpt-only.sh`, and
  `config/coding-agents/codex-adapter.md` (with their dedicated tests).
- Rewrite `config/coding-agents/ROUTING.md` and `claude-adapter.md` for the
  single raw-harness mode.
- `install-policy.py` now converges only the Claude adapter, always removes the
  retired managed block from `~/.codex/AGENTS.md`, strips owned
  `ccdex-gpt-only` hook registrations without re-adding them, and keeps the
  Fable model pin.
- `scripts/install-claude-clients.sh` installs only `cc` plus the policy link,
  and removes retired ccdex artifacts (bin symlinks, hook symlink, user MCP
  registration) when it finds them.
- `verify-routing` now asserts the retired artifacts are absent instead of
  present.

## Impact

- Affected specs: `claude-harness-codex` (client/transport requirements
  removed; bridge requirements retained), `deployment-installation`
  (installer requirement narrowed to `cc` + policy + retired-artifact
  cleanup).
- Affected code: `clients/`, `config/coding-agents/`,
  `scripts/install-claude-clients.sh`, `tests/unit`, `tests/integration`.
- Not affected: proxy runtime, `/v1/ccdex/messages` server bridge and its
  integration tests, `clients/claude-lb-launch` behavior and tests.
