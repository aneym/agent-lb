# Canonical coding-agent modes

This file is the single source of truth for coding-agent model routing on this
computer. Its stable host-neutral path is
`~/.agents/policy/coding-agents/ROUTING.md`. Host instructions and skills are
adapters: when they disagree with this file, this file wins.

## Northstar (owner, 2026-07-15)

One expensive driver, one adversarial partner, cheap fast fan-out. Fable-level
intelligence drives (orchestrates, decides, verifies) while spending as few of
its own tokens as possible: subagents and subagent workflows do the volume
work quickly and in parallel. GPT Sol at xhigh effort is the adversarial
review subagent — the driver and it jam to decide direction on substantive
calls. The alias registry exists to serve this shape; judge every routing
change against it.

## Mode: raw Claude Code harness with canonical seats (2026-07-15)

Claude Code, entered through `cc`, is the only coding harness. Fable/high is
the driver: it coordinates, decides, reconciles, and verifies using the native
harness — `Agent`/`Workflow` subagents, skills, and hooks.

The canonical seat lineup (owner, 2026-07-15) is fixed per SEAT, not chosen
per task. Sol seats are served by agent-lb's Messages-route model aliases
(commit 5c173b29):

| Seat                   | Agent definition                  | Model                | Effort            |
| ---------------------- | --------------------------------- | -------------------- | ----------------- |
| Driver (main loop)     | —                                 | `claude-fable-5`     | high              |
| Explore / scouts       | `~/.claude/agents/Explore.md`     | `sonnet`             | inherit           |
| Implementer            | `~/.claude/agents/implementer.md` | `gpt-5.6-sol-medium` | medium, fast tier |
| Verifier (adversarial) | `~/.claude/agents/verifier.md`    | `gpt-5.6-sol-xhigh`  | xhigh, fast tier  |

Other subagents default to `inherit`. Ad-hoc model switching outside these
seats stays forbidden — no Codex-host dispatch, Composer, Gemini, or other
model products as coding lanes, and no per-task improvisation of the lineup.
Changing the lineup means editing this table (and the agent files), not
overriding it in a session.

The Codex dispatch stack remains retired (2026-07-15): the `ccdex` entry
point, the codex skills and plugin, the `ccdex-worker` MCP transport, and the
`ccdex-gpt-only` hook. Sol seats run INSIDE the Claude Code harness via the
alias bridge, not through a second harness.

## Operating contract

1. One harness, one coordinator. Fable owns the user conversation,
   decomposition, dispatch, reconciliation, and final verification.
2. Delegated subagents return a bounded closeout: conclusion, evidence,
   verification, next action, and artifact paths. The coordinator
   independently checks the acceptance criteria.
3. Subagent models are pinned by the canonical seat table above, in the agent
   definitions themselves. Any other model override is an exception that must
   state its cost or capability reason in the definition.

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
