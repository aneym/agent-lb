# Canonical coding-agent modes

This file is the single source of truth for coding-agent model routing on this
computer. Its stable host-neutral path is
`~/.agents/policy/coding-agents/ROUTING.md`. Host instructions and skills are
adapters: when they disagree with this file, this file wins.

## Mode: raw Claude Code harness (2026-07-15)

Claude Code, entered through `cc`, is the only coding harness. Fable/high is
the session model: it coordinates, plans, implements, and verifies using the
native harness — `Agent`/`Workflow` subagents, skills, and hooks. Subagents run
on Claude models and default to `inherit`.

The Codex dispatch stack is retired (2026-07-15): the `ccdex` entry point, the
codex skills and plugin, the `ccdex-worker` MCP transport, and the
`ccdex-gpt-only` hook. Do not reintroduce a second engineering lane or switch
models by task type — no Codex, Composer, Gemini, or other model products as
coding lanes.

Planned evolution: worker model aliases (for example `worker-gpt`) served by
the agent-lb alias registry, so a subagent can pin a non-Claude account pool
while the harness stays Claude Code. Until that registry ships, subagent model
choice is a Claude-model choice.

## Operating contract

1. One harness, one coordinator. Fable owns the user conversation,
   decomposition, dispatch, reconciliation, and final verification.
2. Delegated subagents return a bounded closeout: conclusion, evidence,
   verification, next action, and artifact paths. The coordinator
   independently checks the acceptance criteria.
3. A model override on a subagent is an exception, not a routing rule: pin a
   model in an agent definition only for a cost or capability reason the
   definition itself states.

## Runtime enforcement

- `clients/claude-lb-launch` defaults `cc` sessions to Fable/high.
- `scripts/install-claude-clients.sh` installs this policy and the `cc`
  client, and removes retired ccdex artifacts (clients, hook, MCP
  registration) when it finds them.

## Validation

Run `~/.agents/policy/coding-agents/verify-routing` for deterministic machine
checks. Routing is not proven by prose alone. A valid rollout also has one
live `cc` response reporting Fable, or an explicit current provider-capacity
blocker.
