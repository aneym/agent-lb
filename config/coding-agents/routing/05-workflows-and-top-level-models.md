## Workflows and top-level models (2026-10-14)

Alex, 2026-10-14 ~02:55Z: "regardless, have the factory fix this without losing quality of everything."
Alex, 2026-10-14 ~02:58Z: "ultracode on is fine - we want workflows to be used and just to have proper rules aorund it."

Alex, 2026-10-14 ~03:00Z: "as long as opus medium is the top level model i think here".

Every top-level tab and every workflow orchestrator runs `opus-latest` at medium, never xhigh, max or Fable by default. Full-tier tabs, including raise, recruiter, factory and rails when full, keep ultracode on. Wake-only tabs keep their current allocation. Workflow helper agents run `sol-latest`. Opus judge and synthesis stages run at medium. Review runs on request only (see Merging, deploy and review); escalate failed work through the existing quality checks. Workflows fan out without an agent cap; host admission throttles only at real memory or disk floors.

Use scripts or Sol for wakes, status digests and polling. Never start a workflow for status, polling or single-file work. Do not wake Claude for a status digest or a routine wake. Task delivery and Alex's requests still follow the tab's tier.

A Workflow returns its result to the thread that started it. Record quota percentage from a matching workflow id in agent-lb unified utilization capture when present; otherwise record the run's tokens and label missing usage. Never infer quota percentage from token counts. Hook context for SessionStart, UserPromptSubmit and PostToolUse is at most 1024 UTF-8 bytes per hook: ids and a one-line preview, full text through `t3_thread_read`. Memory and learnings use short pointers with commands for full text.
