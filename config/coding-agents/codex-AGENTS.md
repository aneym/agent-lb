# Working with Alex

Deliver the requested outcome end to end with your own judgment; make reversible decisions within scope. Preserve unrelated work. Report what is done, the evidence, and what is blocked or unverified, briefly. No report or summary Markdown files unless the brief names the path.

{{inline: claude-config rules/floors.md}}

Every turn in a T3 thread ends through the a0 skill (`~/.agents/skills/a0/SKILL.md`).

Main checkouts stay on the default branch; other branches get a worktree (`git worktree add <repos>/<repo>-wt/<topic> -b <branch> origin/main`, or `delegate_task` with `isolation: "worktree"`). Never `git switch` a shared checkout. Landing: agent-rails via `scripts/studio_merge.py merge <branch>`; other repos fast-forward to origin/main.

Stop only the PID you recorded when you started a process (`echo $!` or a pidfile), with `kill <pid>`; free a port with `port-free <port>`; leave orphans to factory-worker-reap.service. Never a parent PID, and never stop processes by name on any host, over ssh too: no pkill, killall, or kill fed by pgrep, pidof, ps or lsof -t (`lsof -t … | xargs kill` killed Tailscale on 2026-10-16).

On macOS pkill and killall treat every word after the first name as another name, not an option: a stray `1` in `pkill -f "sleep 600" -P 1`, sent over ssh, stopped most of Alex's running programs on 2026-11-10.

Machines and browser: `~/.agents/shared/MACHINES.md`. CLIs: `~/.agents/shared/CLI-INDEX.md`. Tests: load `test-audit` before writing or changing one. Research Alex asks for: run `x-pass "<question>"` once.

Delegation: delegate bounded work to cheaper workers, answer simple asks directly. `~/.agent-lb/bin/route resolve <alias>` gives the model id; set `reasoning_effort` and `fork_turns: "none"`. Luna for mechanical, Terra for bounded implementation, Sol for hard diagnosis. Details: `~/.claude/docs/codex-delegation.md`.
