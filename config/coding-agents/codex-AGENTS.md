# Working with Alex

Deliver the requested outcome end to end with your own judgment; make reversible decisions within scope. Preserve unrelated work. Report what is done, the evidence, and what is blocked or unverified, briefly. No report or summary Markdown files unless the brief names the path.

{{inline: claude-config rules/floors.md}}

Every turn in a T3 thread ends through the a0 skill (`~/.agents/skills/a0/SKILL.md`).

Main checkouts stay on the default branch; other branches get a worktree (`git worktree add <repos>/<repo>-wt/<topic> -b <branch> origin/main`, or `delegate_task` with `isolation: "worktree"`). Never `git switch` a shared checkout. Landing: agent-rails via `scripts/studio_merge.py merge <branch>`; other repos fast-forward to origin/main.

Never free a port with `lsof -t … | xargs kill`, `pkill -9 -f` or `killall -9` (it killed Tailscale on 2026-10-16). Use `port-free <port>` or `kill <pid>` for a pid you started.

Machines and browser: `~/.agents/shared/MACHINES.md`. CLIs: `~/.agents/shared/CLI-INDEX.md`. Tests: load `test-audit` before writing or changing one. Research Alex asks for: run `x-pass "<question>"` once.

Delegation: delegate bounded work to cheaper workers, answer simple asks directly. `~/.agent-lb/bin/route resolve <alias>` gives the model id; set `reasoning_effort` and `fork_turns: "none"`. Luna for mechanical, Terra for bounded implementation, Sol for hard diagnosis. Details: `~/.claude/docs/codex-delegation.md`.
