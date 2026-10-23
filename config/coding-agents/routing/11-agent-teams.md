## Agent teams (2026-10-16)

Alex, 2026-10-16 ~15:20Z: "use agent teams, keep this lane clean, tell factory this should be the default. the main agent needs to lead teammates for parallelization, teammates likely on opus, you can dispatch workflows that are recommended though"
- **Agent teams are the default.** A lane's main agent leads Opus teammates for parallel work and keeps its own lane clean: it plans, dispatches and judges. Opus teammates plan, spec and judge; hands (code, fix-forward, landing, probes, tests, sweeps) go to Codex seats through seat-run (2026-10-20, routing-use: about 1,100 Opus hands dispatches in the week to 2026-10-20 while Codex sat at 97% weekly headroom).
- **Stop after the result.** A teammate that returned its result is stopped with TaskStop, never kept warm. If needed again, write a handoff to the lane dir and spawn a fresh `<name>-2` with the handoff instead of resuming the old context.
- **Workflows stay available** where they are recommended; the main agent dispatches them under the workflow rules above.

