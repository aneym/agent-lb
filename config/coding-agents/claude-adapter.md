<!-- agent-lb:coding-agent-routing:start -->

## Coding-agent routing

The canonical policy is `~/.agents/policy/coding-agents/ROUTING.md`; it wins over
project instructions, orchestration notes, and skills when they disagree.

- Claude Code is the only coding harness. Fable 5/high drives and routes; the canonical
  seats are Explore→gpt-5.6-sol-medium, implementer→gpt-5.6-terra-medium (on
  trial since 2026-08-20 — the coordinator audits every implementer closeout),
  verifier→gpt-5.6-sol-xhigh (agent-lb alias bridge), fixed per seat. Design
  (frontend-designer) is Fable always.
- The Codex dispatch stack is retired (2026-07-15). No ad-hoc model switching
  outside the canonical seats; changing the lineup means editing ROUTING.md
  and the agent files.
- Delegated subagents return a bounded closeout; the coordinator independently
  verifies the acceptance criteria.
- `opus` is the explicit Opus 5 `[1m]` entrypoint when genuine 1M context is
  more valuable than Fable's orchestration.

<!-- agent-lb:coding-agent-routing:end -->
