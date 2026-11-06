## Models (2026-11-06)

`~/.claude/rules/workflow.md` sets which model does what. This page adds only what it does not say.

- **Mac clicks (2026-10-17).** Click macOS consent and TCC prompts, Privacy & Security toggles, system dialogs and app permission prompts yourself with Terra computer use (`computer-use-terra-local` on Studio); log each with `ledger.event` and a screenshot; record `~/.open-factory/approvals/alex-mac-clicks-2026-10-17.md`. Never Touch ID or codes. Computer use never runs on Alex's desktop.
- No tool call blocks past 270 s: wait with `t3_thread_wait` (bounded), `task_status`, or a background shell or Monitor plus `t3_background_wait`; no gh poll loops.
