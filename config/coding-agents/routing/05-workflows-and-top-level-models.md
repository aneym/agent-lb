## Workflows and top-level models (2026-10-14)

Alex, 2026-10-14 ~02:55Z: "regardless, have the factory fix this without losing quality of everything."
Alex, 2026-10-14 ~02:58Z: "ultracode on is fine - we want workflows to be used and just to have proper rules aorund it."

Alex, 2026-10-14 ~03:00Z: "as long as opus medium is the top level model i think here".

Alex, 2026-10-29 ~16:07Z: "likely we need to use opus medium without ultracode by default? ... if we use opus in ultracode, i still want the workflows to use gpt properly to prevent overuse of opus. opus is for architecture and reviewing and scoping i think, so we get the brains out of it."

Every top-level tab, lane and workflow orchestrator runs `opus-latest` at medium with ultracode off by default, never xhigh, max or Fable. A lead turns on high or ultracode for one task of architecture, scoping, review verdicts or visual judgment and records why; an explicit setting from Alex stands. Wake-only tabs keep their current allocation. Every Workflow `agent()` call names its route: helpers pass `model: 'gpt-6.1-sol'` or a typed GPT `agentType`, and only judge, synthesis or visual stages pass `model: 'opus'` at `effort: 'medium'`. The claude-config `child-routing` hook fills a missing route on Claude leads (`gpt-6.1-sol` medium for unrouted `agent()` calls, `sol-general` for untyped Agent calls, medium for Claude children without an effort), whatever the lead's ultracode setting; `~/.agent-rails/factory/child-routing.json` `{"mode": "warn"}` or `"off"` rolls it back, and each decision is logged to `child-routing.jsonl`. Proof of a route is the served model in the child transcript and on the agent-lb row, never the label. Review runs on request only (see Merging, deploy and review); escalate failed work through the existing quality checks. Workflows fan out without an agent cap; host admission throttles only at real memory or disk floors.

Use scripts or Sol for wakes, status digests and polling. Never start a workflow for status, polling or single-file work. Do not wake Claude for a status digest or a routine wake. Task delivery and Alex's requests still follow the tab's tier.

A Workflow returns its result to the thread that started it. Record quota percentage from a matching workflow id in agent-lb unified utilization capture when present; otherwise record the run's tokens and label missing usage. Never infer quota percentage from token counts. Hook context for SessionStart, UserPromptSubmit and PostToolUse is at most 1024 UTF-8 bytes per hook: ids and a one-line preview, full text through `t3_thread_read`. Memory and learnings use short pointers with commands for full text.
