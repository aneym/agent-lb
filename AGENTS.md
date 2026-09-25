# agent-lb: rules for agents

agent-lb is Alex's fork of codex-lb: a local load balancer that routes Claude
Code, Codex and other clients across subscription accounts. Its main checkout
(`/Volumes/StudioExt/repos/agent-lb`) runs the live launchd service
`com.aneyman.agent-lb` on `http://127.0.0.1:2455`. The long form of these rules,
with the review trapdoors and the reasons, is
[`.agents/conventions/agents-long-form.md`](.agents/conventions/agents-long-form.md).
New machine setup: `GETTING-STARTED.md`. Account work: the
`agent-lb-account-operator` skill.

## Rules

1. Work lands on `main` (this fork has no feature-branch flow). Leave the main
   checkout on `main`; use a worktree when it has someone else's edits.
2. Validated changes ship without asking: commit, `git push origin main`,
   restart, and fast-forward other machines. "Validated" means exercised:
   `ruff check app clients`, the relevant tests, and the affected endpoint
   answering after restart. Launcher changes: `py_compile` plus a
   `CLAUDE_LB_DRY_RUN=1` round trip.
3. Restart or deploy into the runtime only with `~/.agent-lb/bin/lb-restart
   --reason "<what>" [--from <worktree> --files <paths>]` (blue/green: new
   connections never wait; lock, health gate, rollback). Plist edits:
   `--reload-plist`. Never `launchctl kickstart`/`bootout` the service or the
   front by hand; upgrade the front with `node scripts/front-hot-swap.mjs`.
4. Anthropic request path (`app/core/anthropic/**`, `app/modules/proxy/anthropic*`):
   never add, remove or reorder system blocks on a Claude Code payload (the
   first block is a billing marker that must stay first). After restart, run
   `python3 scripts/claude_cache_eval.py` and keep its receipt.
5. OpenSpec gates behavior, API, schema, CLI and routing changes: create
   `openspec/changes/<slug>/` first and run `openspec validate --specs`. Put
   context in OpenSpec, not `docs/`; never edit `CHANGELOG.md`.
6. Secrets (`GITHUB_TOKEN` and account credentials) never go in commits, logs
   or output.
7. PRs from collaborators follow [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md)
   merge gates, including `python3 scripts/local_ci.py status <sha>`.

Coding-agent routing canon: `config/coding-agents/ROUTING.md`. Quota snapshots
are advisory (`agent-lb status --json`); never deny a launch on an estimate.
