## Context

`cc` and `ccdex` use the same Claude Code executable but are different control planes. Normal `cc` should spend Fable context on planning, orchestration, and review while delegating implementation through the direct CCDEX worker. `ccdex` should remain GPT-only. Today the shell wrapper, global Claude settings, launcher defaults, workflow literals, and model-selective proxy rewrite can disagree, so a GPT root may create Claude children and a direct normal launcher may claim Fable quota while executing the globally configured Opus model.

Machine-wide prose and skills are outside the repository's deployment boundary, but the launcher and installer must provide hard runtime guarantees. The machine policy can then be concise and host-aware without being the only enforcement mechanism.

## Goals / Non-Goals

**Goals:**

- Make the effective parent model deterministic for both launcher modes.
- Make CCDEX fail closed before any non-GPT child inference reaches Anthropic.
- Make the durable CCDEX worker discoverable from normal Claude Code.
- Validate both configuration and recorded session behavior.

**Non-Goals:**

- Dynamically choose models by frontend/backend task type.
- Rename or pin the native Codex model in prose; its runtime configuration remains authoritative.
- Make the worker transport an operating-system sandbox.
- Migrate unrelated MCP servers, skills, or Claude settings.

## Decisions

### Pin the normal default in the launcher

The launcher adds canonical Fable and high effort only when normal `cc` has no caller override. This makes direct executable use agree with account selection and survives shell-wrapper drift. Explicit user overrides remain possible in normal Claude Code. Relying only on `~/.zshrc` or `settings.json` was rejected because non-interactive and direct paths can bypass either source.

### Reject non-canonical models at the CCDEX HTTP shim

In CCDEX mode the local shim returns an Anthropic-shaped error for any Messages inference body whose model differs from the canonical compatibility model. The existing selective passthrough behavior is removed. A PreToolUse hook remains useful feedback, but hook-only enforcement was rejected because workflows, resumes, plugins, and future tool surfaces may bypass it.

### Install the worker as a named user-scoped MCP server

A dedicated client installer creates stable links under `~/.local/bin` and uses Claude's MCP CLI to converge a single `ccdex-worker` registration. The registration points to the stable installed path instead of a transient process. Folding this into service installation was rejected because client configuration and launchd service lifecycle have different rollback boundaries.

### Keep one machine policy with thin host adapters

The canonical mode matrix lives under `~/.agents/policy/coding-agents/`. Claude and Codex always-loaded files state only their host adapter and point to that policy; routing skills describe invocation mechanics without redefining model ownership. Runtime correctness remains testable from this repository even if prose drifts.

## Risks / Trade-offs

- [A Claude plugin legitimately requests a Claude child inside CCDEX] -> Reject it explicitly; operators must run that task from normal `cc` instead of silently changing pools.
- [Fable quota is unavailable] -> Normal `cc` reports the planner-capacity blocker; it does not substitute another Claude model. Native Codex can continue without the optional consultation.
- [Claude MCP CLI format changes] -> Keep installer calls narrow, validate `claude mcp get`, and fail without rewriting unrelated MCP configuration.
- [Existing user links or MCP registration point elsewhere] -> Preview exact targets and replace only the three named links plus the `ccdex-worker` entry.

## Migration Plan

1. Land and validate the worker transport.
2. Land launcher default/fail-closed behavior and client installer.
3. Run the installer on this machine and verify MCP discovery.
4. Reconcile the canonical machine policy and thin adapters.
5. Launch fresh normal `cc` and CCDEX probes and inspect recorded root/child models.
6. Roll back by restoring the previous launcher, removing `ccdex-worker` registration explicitly, and leaving job artifacts untouched.

## Open Questions

None for this change.
