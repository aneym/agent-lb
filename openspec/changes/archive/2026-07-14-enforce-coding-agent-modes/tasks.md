## 1. Launcher Profiles

- [x] 1.1 Make normal Claude Code default to canonical Fable/high in the launcher when no explicit controls are supplied.
- [x] 1.2 Reject non-canonical Messages inference in CCDEX before Anthropic routing while preserving the local token counter.
- [x] 1.3 Add focused regression tests for normal defaults, caller overrides, CCDEX stripping, and non-GPT child rejection.

## 2. Worker Installation

- [x] 2.1 Add an idempotent previewable installer for `cc`, `ccdex`, and `ccdex-worker-mcp` links plus user-scoped Claude MCP registration.
- [x] 2.2 Add deterministic installer tests that do not mutate the real user configuration.
- [x] 2.3 Install the clients on this machine and verify the exact links and connected MCP registration.

## 3. Machine Policy

- [x] 3.1 Create one canonical mode policy under `~/.agents/policy/coding-agents/` with a mode matrix and validation command.
- [x] 3.2 Reduce Claude and Codex always-loaded routing text and routing skills to thin mode-specific adapters without task-based model switching.
- [x] 3.3 Repair the stale Claude Skill hook executable path and verify routing-hook denial, adapter parity, and relevant skill references.

## 4. End-to-End Verification

- [x] 4.1 Run byte-compilation, Ruff, focused tests, and strict OpenSpec validation.
- [x] 4.2 Exercise a fresh normal Claude Code launch and prove Fable selection (or record the current provider-capacity denial) plus a successful CCDEX worker result.
- [x] 4.3 Exercise a fresh CCDEX launch and prove its GPT parent plus denial of Claude Agent/Workflow or non-GPT child inference.
- [x] 4.4 Review final repository and machine-config diffs, commit and push validated repository changes, and record any unavailable live proof honestly.
