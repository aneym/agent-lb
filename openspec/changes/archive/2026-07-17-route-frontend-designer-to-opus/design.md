## Context

The routing policy names `~/.claude/agents/frontend-designer.md` as a canonical seat, but that file is currently machine-local rather than installed from the repository. Its Fable model therefore both consumes the scarce driver pool and can drift independently of the declared policy.

## Goals / Non-Goals

**Goals:**

- Make Opus the single canonical model for frontend design direction and critique.
- Version the full agent definition and converge it through the existing checkpointed policy installer.
- Preserve unrelated Claude agent definitions and make preview, repeated install, and uninstall deterministic.

**Non-Goals:**

- Changing the main Claude Code driver or planner model.
- Changing the designer's tools, responsibilities, or bounded-output contract.
- Redesigning planner economics; that decision follows the separate live-results audit.

## Decisions

1. Use `opus` in agent frontmatter and `claude-opus-4-8` in the routing table. The frontmatter alias follows Claude Code's stable agent-definition contract, while the table records the currently intended concrete family.
2. Store the canonical definition at `config/coding-agents/agents/frontend-designer.md` and have `install-policy.py` copy it into the user's Claude agents directory. This keeps installation preservation-safe and makes the repository the source of truth.
3. Treat the managed agent like the existing managed Markdown and JSON surfaces: preview reports drift, install checkpoints any existing file before replacement, records explicit ownership, and repeated install is byte-stable. Uninstall removes only an owned, byte-identical managed file. A user-modified or never-owned file is preserved rather than deleted.

## Risks / Trade-offs

- **Opus availability can differ from Fable availability** -> Surface the configured model deterministically; do not silently fall back to Fable.
- **A user may customize the installed designer file** -> Checkpoint before overwrite and refuse to delete a divergent file during uninstall.
- **The explicit model family can age** -> Keep the Claude frontmatter alias stable and update the routing table when the canonical concrete model changes.

## Migration Plan

Run the normal Claude client installer. It checkpoints the current machine-local designer definition, writes the versioned Opus definition, and leaves other agent files untouched. Rollback is the checkpointed prior file or the previous repository revision followed by reinstall.

## Open Questions

None.
