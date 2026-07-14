# Canonical coding-agent modes

This file is the single source of truth for coding-agent model routing on this
computer. Its stable host-neutral path is
`~/.agents/policy/coding-agents/ROUTING.md`. Host instructions and skills are
adapters: when they disagree with this file, this file wins.

## Modes

| Entry point | Coordinator | Planning and review | Implementation and investigation | Forbidden routing |
| --- | --- | --- | --- | --- |
| `cc` (normal Claude Code) | Fable, high effort | Fable | Codex/GPT workers through `ccdex-worker`, Codex skills, or `codex exec` | No Composer, Gemini, Haiku, Sonnet, Opus, or task-based model switching |
| `ccdex` (GPT compatibility host) | Canonical GPT, high effort | Canonical GPT | Canonical GPT/Codex only | No Claude `Agent` or `Workflow`; no Claude-model Messages request; no non-GPT fallback |
| Native Codex | Codex using the current user configuration | Codex coordinates; for substantive planning, consult a tracked Fable planner when available | Codex | No non-Fable/non-Codex engineering lane; no task-based model switching |

## Operating contract

1. Select the entry-point mode once. Do not change models because the task is
   frontend, backend, research, review, or computer use.
2. In normal `cc`, Fable owns the user conversation, decomposition, written
   worker contracts, reconciliation, and final verification. Codex/GPT owns
   hands-on execution. If the GPT worker transport is unavailable, report the
   blocker; do not silently substitute another Claude model.
3. In `ccdex`, GPT owns the entire loop. A Workflow has no safe inherited model:
   each embedded agent chooses its own model, so Claude `Agent` and `Workflow`
   are prohibited. Fan-out uses `ccdex-worker` or direct Codex processes.
4. In native Codex, Codex remains coordinator. Fable planning is consultation,
   not a handoff of coordination or implementation.
5. Delegates inherit the current configured model, reasoning, and service tier.
   Do not pin a GPT version in skills or prompts unless the transport itself
   requires a compatibility identifier.
6. Every delegated lane returns a bounded closeout: conclusion, evidence,
   verification, next action, and artifact paths. The coordinator independently
   checks the acceptance criteria.

## Runtime enforcement

- `clients/claude-lb-launch` defaults normal `cc` to Fable/high and forces
  `ccdex` to the compatibility model/high.
- In `ccdex`, the local launcher rejects noncanonical Messages inference before
  it can select or contact an Anthropic account.
- `~/.claude/hooks/ccdex-gpt-only.sh` rejects Claude `Agent` and `Workflow` calls
  as an early, human-readable guardrail.
- `scripts/install-claude-clients.sh` installs this policy, `cc`, `ccdex`, and
  `ccdex-worker-mcp`, and registers the worker MCP for normal Claude Code.

## Validation

Run `~/.agents/policy/coding-agents/verify-routing` for deterministic machine
checks. Routing is not proven by prose alone. A valid rollout also has one live
normal-`cc` response reporting Fable and one live `ccdex` response reporting
GPT, or an explicit current provider-capacity blocker for either.
