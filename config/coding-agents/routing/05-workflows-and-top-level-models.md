## Workflows and top-level models (2026-10-14)

Alex, 2026-10-14 ~02:55Z: "regardless, have the factory fix this without losing quality of everything."
Alex, 2026-10-14 ~02:58Z: "ultracode on is fine - we want workflows to be used and just to have proper rules aorund it."

Alex, 2026-10-14 ~03:00Z: "as long as opus medium is the top level model i think here".

Every top-level tab and every workflow orchestrator runs `opus-latest` at medium, never xhigh, max or Fable by default. Full-tier tabs, including raise, recruiter, factory and rails when full, keep ultracode on. Wake-only tabs keep their current allocation. Workflow helper agents run `sol-latest`. Opus judge and synthesis stages run at medium. Review runs on request only (see Merging, deploy and review); escalate failed work through the existing quality checks. Workflows fan out without an agent cap; host admission throttles only at real memory or disk floors. `factory quota show --json` emits the rules and `route workflow-args` emits the stage options.

Use scripts or Sol for wakes, status digests and polling. Never start a workflow for status, polling or single-file work. Do not wake Claude for a status digest or a routine wake. Task delivery and Alex's requests still follow the tab's tier. `factory quota tick` persists tight mode when any Claude account's 5h utilization passes 70%, retaining explicit tab allocations.

Each Workflow completion appends to `~/.open-factory/state/workflow-readback.jsonl`, or `FACTORY_HOME/workflow-readback.jsonl`. Record quota percentage from a matching workflow id in agent-lb unified utilization capture when present; otherwise record the run's tokens and label missing usage. Never infer quota percentage from token counts. Hook context for SessionStart, UserPromptSubmit and PostToolUse is at most 1024 UTF-8 bytes per hook. Deliver bulletin ids and a one-line preview, keep overflow queued, and retrieve full text with `lane-post read <id>`. Memory and learnings use short pointers with commands for full text.

