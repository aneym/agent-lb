## Why

The frontend-designer seat currently consumes the same scarce Fable pool as the main driver and planner lanes. Design work needs a high-judgment Claude model, but it should use the Opus pool so visual critique cannot accelerate Fable exhaustion.

## What Changes

- Route the canonical Claude Code `frontend-designer` seat to Opus instead of Fable.
- Version and install the frontend-designer agent definition so the declared routing policy and live machine state cannot drift.
- Verify installation, preview, idempotence, and removal of the managed agent definition.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `claude-harness-codex`: Define Opus as the canonical model for frontend design-direction and critique children.
- `deployment-installation`: Converge the versioned frontend-designer agent definition into Claude Code while preserving unrelated agents.

## Impact

- `config/coding-agents/` gains the canonical frontend-designer definition and updated routing policy.
- The policy installer manages `~/.claude/agents/frontend-designer.md` with checkpointed, preservation-safe writes.
- Installer and routing-verifier tests gain assertions for the Opus designer seat.
