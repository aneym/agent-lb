## 2026-11-13 — Explicit manual Claude reset-cap override

Alex explicitly approved the menubar reset fix and manual weekly confirmation on
2026-11-13 (implementation task and parent relay; local approval record
`~/.open-factory/approvals/alex-menubar-reset-fix-2026-11-13.json`). The automatic
weekly allowance is unchanged. The authenticated manual consume path may bypass
its weekly cap only with `overrideWeeklyLimit: true`; the existing daily override
remains separate. Both are audited, manual-only and Claude-only. Ownership,
subscription, 95% usage, provider grant eligibility, serialization and unknown
outcome guards remain. This supersedes the all-callers weekly cap for explicitly
confirmed manual actions only. The menubar selects a real grant, displays its
scope and confirms the exception; it never infers full-account scope from a union.
