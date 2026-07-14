## Context

The first policy rollout installed executable and policy symlinks but relied on manually edited global files already present on the originating machine. On the MacBook, the same installer therefore left legacy model-routing prose, a noncanonical model value, and no CCDEX guard hook. Those files also contain substantial unrelated machine-specific configuration that must not be replaced wholesale.

## Goals / Non-Goals

**Goals:**

- Converge only the routing-owned Markdown blocks, Claude model field, CCDEX hook registration, and versioned hook file.
- Preserve unrelated prose, JSON keys, permission rules, hooks, and machine-specific values byte-for-byte where practical.
- Make installation idempotent, previewable, testable in temporary homes, and verifiable on both machines.

**Non-Goals:**

- Synchronizing every global Claude/Codex setting across machines.
- Removing unrelated skills, hooks, permissions, environment variables, or shell configuration.
- Restarting the agent-lb server, because this change affects client configuration only.

## Decisions

1. Add a focused Python policy installer under `config/coding-agents/` and invoke it from the shell client installer. Python provides reliable Markdown block migration and JSON preservation without depending on platform-specific `sed` behavior.
2. Manage routing prose with explicit `agent-lb` start/end markers. On first install, migrate the known legacy Claude orchestration section and any existing Codex Fable/Codex routing section; otherwise insert a short canonical adapter without rewriting adjacent sections.
3. Modify `settings.json` structurally: set only `model`, remove prior registrations of the same CCDEX hook command, and append one canonical `PreToolUse` registration. All other JSON values remain semantically unchanged.
4. Version `ccdex-gpt-only.sh` beside the canonical policy and symlink the user hook path to it. Existing regular files are preserved once at `.pre-agent-lb`; a conflicting pre-existing backup remains a fail-closed condition.
5. Parameterize home/config paths so unit tests exercise first install, legacy migration, repeated install, preview, and preservation without touching the real user home.

## Risks / Trade-offs

- [Risk] A previously unseen legacy routing heading remains outside the managed block. → The canonical adapter explicitly wins on conflict, known conflicting forms are migrated, and the verifier checks the installed pointer and settings.
- [Risk] JSON formatting changes when written structurally. → Formatting is normalized, but keys and values outside the owned model/hook entries are retained and tests assert semantic preservation.
- [Risk] A user intentionally customized the CCDEX hook path. → Only commands that name `ccdex-gpt-only.sh` are replaced; unrelated hooks are preserved.
- [Risk] Installation stops after some links are updated. → All policy source files are validated before mutation, and rerunning installation is idempotent.
