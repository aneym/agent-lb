## Why

Every Bash tool call ran 15 PreToolUse hooks and 1 PostToolUse hook, each its own `/bin/sh -c` tree into bash, python or jq. With about 56 Claude Code sessions on Studio that was roughly 300 new processes a second at peak (factory-operations studio-load-fix U1, 2026-10-07). The hooks are safety floors for every session, so the fix must keep every allow, deny, exit code and message exactly as it is today.

## What Changes

- Add `config/coding-agents/hooks/hook-dispatch.py`, installed at `~/.claude/hooks/hook-dispatch.py`. One process per Claude Code hook entry runs every hook of one (event, matcher) from `~/.claude/hooks/dispatch/registry.json`, in config order:
  - in-process: Python guards on the reviewed list whose source has no hazard (fork, exec, threads, exit handlers, signals, bare except), pinned to the sha256 the parity fixture passed on; a changed guard runs as its own process until the fixture reruns;
  - filtered: pinned shell guards and inline commands (sha256 of the exact bytes) skipped when a prefilter proves they cannot act on the input; this gates the PostToolUse `npx prettier --find-config-path` hook on a prettier config existing near the file;
  - rewriter: `rtk hook claude`, skipped when a guard denied or a later guard rewrote the input, exec'd into when it is the only process still needed;
  - external: everything else, exactly as configured, with its own timeout.
- The answer follows Claude Code 2.1.293's per-hook rules: blocks win and their messages join in config order, an exit 2 keeps the guard's own `[<command>]: <stderr>` text through a JSON block reason, JSON answers merge (deny > ask > allow, contexts joined, last updatedInput wins).
- A missing or inconsistent registry fails closed for PreToolUse (exit 2 plus a JSON deny naming the rollback command) and is a non-blocking error for the other events.
- Add `config/coding-agents/hooks/hook-dispatch-parity.py`, the parity fixture: every case runs through the per-hook config and the dispatcher in fresh sandbox homes and compares each guard's result, the effect Claude Code builds, and the files left behind; it also measures processes per Bash call and synthetic crash, hang and fail-closed guards.
- `install-policy.py --hook-dispatcher on|off`. `on` folds PreToolUse, PostToolUse, UserPromptSubmit and Stop groups with a plain matcher into one dispatcher entry per matcher, only after the parity fixture passes against this home's guards, and keeps the per-hook config verbatim in the registry. `off` restores it verbatim. Without the flag the state is sticky: a folded config is refolded over only the groups the fixture passed on, and a changed dispatcher or in-process guard reruns the fixture, falling back to the per-hook config if it fails. Regex matchers, AskUserQuestion, non-command hooks and lone hooks that would spawn anyway stay per-hook. The dispatcher and registry are written before settings point at them and removed after settings stop.

## Impact

- Capability: `coding-agent-hooks` (new).
- No app or service code change; agent-lb is not restarted. Only `~/.claude/settings.json` hooks, `~/.claude/hooks/hook-dispatch*.py` and `~/.claude/hooks/dispatch/` change, through the installer.
- Not money path. Rollback: `python3 ~/.agents/policy/coding-agents/install-policy.py --hook-dispatcher off`.
