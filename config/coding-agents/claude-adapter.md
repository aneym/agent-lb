<!-- agent-lb:coding-agent-routing:start -->

## Coding-agent routing

The canonical policy is `~/.agents/policy/coding-agents/ROUTING.md`; it wins over
project instructions, orchestration notes, and skills when they disagree.

- Claude Code is the only coding harness. Fable/high coordinates, plans,
  implements, and verifies with native subagents.
- The Codex dispatch stack is retired (2026-07-15). Do not route work to other
  model products or switch models by task type.
- Delegated subagents return a bounded closeout; the coordinator independently
  verifies the acceptance criteria.

<!-- agent-lb:coding-agent-routing:end -->
