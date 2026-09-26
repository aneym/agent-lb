# agent-lb: rules for agents

agent-lb is a fork of codex-lb: a local load balancer that routes Claude
Code, Codex and other clients across subscription accounts. Its main checkout
on the serving machine runs the live launchd service (label in
`scripts/install-service.sh`) on `http://127.0.0.1:2455`. The long form of these rules,
with the review trapdoors and the reasons, is
[`.agents/conventions/agents-long-form.md`](.agents/conventions/agents-long-form.md).
New machine setup: `GETTING-STARTED.md`. Account work: the
`agent-lb-account-operator` skill.

## Rules

1. Work lands on `main` (this fork has no feature-branch flow). Leave the main
   checkout on `main`; use a worktree when it has someone else's edits.
2. Validated changes ship without asking: commit, `git push origin main`,
   restart, and fast-forward other machines. "Validated" means exercised (the
   change's one check, `ruff check app clients`, the relevant tests, the
   affected endpoint answering after restart) and reviewed (rule 9). Launcher
   changes: `py_compile` plus a `CLAUDE_LB_DRY_RUN=1` round trip.
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
8. Money path: the request paths and payload handling, account credentials and
   custody, auth, the selector and quota marks, migrations, `lb-restart` and
   the front, and seat routing (`clients/route`, the seat guard). The surfaces
   are listed in the long form; when in doubt, it is on the money path.
9. Every change names one check that proves it. Before push, a fresh verifier
   from the vendor that did not write it reviews it (`verifier` for GPT,
   Cursor, Devin, GLM or Kimi authors; `codex-verifier`, Sol at xhigh, for Claude
   authors), and the brief names the author vendor. Money-path changes get
   three lenses (payload, accounts, release) at xhigh; any one FAIL blocks.
10. Never push after a FAIL: fix and re-verify the final diff, or record
    `Verify-override: <reason>` in the commit. The commit carries `Seat:` and
    `Verified-by:` trailers with the diff id. Nothing checks them; they are
    the record.
11. An incident fix ships its fixture test or check in the same commit.
12. Rules change with evidence: a dated `DECISIONS.md` entry. History stays in
    git.
13. Every request log row names a person and a machine (2026-09-26): the
    person from the key's member, else the owner when the request comes from
    an owner machine, else `unknown`; the machine from the client address the
    auth layer resolves. Short handles only, never emails; the relay's own
    work is `internal`. Details in the long form.

Coding-agent routing canon: `config/coding-agents/ROUTING.md`. Quota snapshots
are advisory (`agent-lb status --json`); never deny a launch on an estimate.
