<!-- agent-lb:coding-agent-routing:start -->

## Coding-agent routing

The canonical policy is `~/.agents/policy/coding-agents/ROUTING.md`; it wins over
project instructions, orchestration notes, and skills when they disagree.

- Claude Code is the only coding harness. Opus 5/high drives; the canonical
  seats are Explore→gpt-5.6-sol-medium, implementer→gpt-5.6-sol-medium,
  verifier→gpt-5.6-sol-xhigh (agent-lb alias bridge), fixed per seat.
- The Codex dispatch stack is retired (2026-07-15). No ad-hoc model switching
  outside the canonical seats; changing the lineup means editing ROUTING.md
  and the agent files.
- Delegated subagents return a bounded closeout; the coordinator independently
  verifies the acceptance criteria.

<!-- agent-lb:coding-agent-routing:end -->
