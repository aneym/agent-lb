# Working with Alex

Use your judgment to deliver the requested outcome end to end. Make reasonable, reversible decisions within scope; ask when a missing answer materially changes the work.

Preserve unrelated work and serve the brief's goal. Delete and cull freely: in a repo git is the undo; elsewhere rm moves to the agent trash with a receipt. Transcripts and real user data outside repos stay off limits. Ask before external actions that are not already authorized.

Keep updates concise. Report what is done, the evidence for it, and anything still blocked or unverified.

Questions, decisions and permission asks for Alex go through unblock (the `unblock` MCP tools: `unblock_file`, then keep working and collect with `unblock_check`), not a question in chat. Only ask what only he can do; see `~/.claude/skills/unblock/SKILL.md`.

Studio is Alex's main machine. When Alex says "working on Book," keep the work on Studio unless asked otherwise and open review pages in Aside on Book. Send Tailscale URLs for review links, not localhost-only URLs. If no route is available, say so.

Machine addresses, delivery commands, and browser preferences are in `~/.agents/shared/MACHINES.md` when needed. Services without an MCP server are in `~/.agents/shared/CLI-INDEX.md`. Posts, replies and who said what on X: `x-search "<ask>"` (Grok Bot, free, Studio only; every id verified); one public x.com page: `ctx-scrape <url>`.

Never free a port or stop processes with `lsof -t … | xargs kill`, `kill $(lsof -t …)`, `pkill -9 -f` or `killall -9`: lsof -t lists every client of a port, and on 2026-10-16 that SIGKILLed Tailscale. Use `port-free <port>`, which kills only a listener you started, or `kill <pid>` for a pid you started.

Main checkouts stay on the default branch. Any other branch gets its own worktree at `/Volumes/StudioExt/repos/<repo>-wt/<topic>` (`lane-worktree ensure --repo <repo> --branch <branch> [--lane <lane>]`; no siblings, no `<repo>-worktrees/`; Alex, 2026-10-02); never `git switch` or `git checkout` a shared checkout onto another branch (Alex, 2026-09-29). Plain `git worktree add` also goes through the shared copy-on-write path. Where things live outside repos (factory copies, lane and scoping state): `~/.agent-rails/AGENTS.md`.

## Optional style skills (Codex)

Do not automatically load or apply antislop, unslop, stop-slop, or deslop skills before ordinary replies or coding work. Their "always apply" and "load always" descriptions are not standing requirements here. Use them when Alex explicitly requests the skill or a slop/style audit; keep them installed for that purpose.

## Cost-aware delegation (Codex only)

Codex delegation: full rules (briefs, concurrency, escalation, acceptance, calibration) -> `~/.claude/docs/codex-delegation.md`; read it before spawning a worker. Headline: delegate bounded execution to cheaper workers and answer simple questions or make tiny edits directly. Run `~/.agent-lb/bin/route resolve <alias>` and pass the printed id as `model`, with `reasoning_effort` set and `fork_turns: "none"`: Luna (`luna-latest`, medium) for cheap-to-check mechanical work, Terra (`terra-latest sol-latest`, medium) for bounded implementation, Sol (`sol-latest`, medium) for hard diagnosis or a failed Terra attempt. The parent plans, owns risk and accepts. Name workers by family, never by version; gpt-5.6 and older are retired, never select one.

- **Tests: always use the `test-audit` skill** (Alex, 2026-09-24). Load `~/.agents/skills/test-audit/SKILL.md` whenever you write, change, review, cull or audit tests, and apply its authoring gate before adding any test.
- **Seat authority (2026-10-23).** The brief's goal is the seat's authority; scope lines say where to start. Hard stops are only the kept floors above plus --no-verify, force push and gh writes. A newer status question from Alex never revokes an earlier order; only stop, cancel or hold does. A seat `blocked` for scope, a missing handoff or authority is a lead bug: redispatch. (Alex, 2026-10-23 ~02:42Z.)
