# agent-lb agent rules: the long form

Updated 2026-09-26. `AGENTS.md` holds the short rules every session loads (owner,
2026-09-25: short, loose instruction files; detail lives in pages agents read when
needed). This page is the full text it was cut from, plus the factory rules at the end.
If they disagree, AGENTS.md wins; fix this page.

---

## Full text of AGENTS.md as of 2026-09-25

## New Machine Onboarding

Setting up agent-lb on a fresh machine (install, service, account connection, client
wiring)? Follow `GETTING-STARTED.md` at the repo root — also available as the
`get-started` skill. The rest of this file is for development work on the codebase.

## Account Operations

For ongoing account-specific work after setup — quota reset checks, stuck or
rate-limited account triage, billing/subscription changes, pause/reactivate
routing, removals, verification, or dedicated browser-profile work — use the
`agent-lb-account-operator` skill and the local
`.agent-lb/account-profiles.json` registry.

## Environment

- Python: .venv/bin/python (uv, CPython 3.13.3)
- GitHub auth for git/API is available via env vars: `GITHUB_USER`, `GITHUB_TOKEN` (PAT). Do not hardcode or commit tokens.
- For authenticated git over HTTPS in automation, use: `https://x-access-token:${GITHUB_TOKEN}@github.com/<owner>/<repo>.git`

## Branch Policy (fork)

**Development on this fork (`aneym/agent-lb`) stays on `main`.** Work directly on `main`; do not create or switch to feature branches unless the user explicitly asks. `feat/anthropic-provider` was consolidated into `main` on 2026-06-09 and is retired. The "do not commit directly to main" convention in `.agents/conventions/git-workflow.md` applies only to upstream (`Soju06/codex-lb`) contributions. The local checkout also runs the live launchd service (label in `scripts/install-service.sh`), so the working tree must remain on `main`.

## Auto-publish (standing authorization)

Validated changes ship immediately — no per-change confirmation. After a change
passes its **validation gate**, commit directly to `main` and `git push origin main`
right away. This is a standing instruction from the repo owner.

"Validated" means the change was actually **exercised**, not merely asserted:

- **Launcher / client (`clients/**`)**: byte-compiles (`python -m py_compile`) and a
  `CLAUDE_LB_DRY_RUN=1` (or real) round-trip behaves correctly.
- **Server (`app/**`)**: imports clean, `ruff check app clients` passes, the relevant
  tests pass, and — for runtime behavior — the service starts and the affected endpoint
  returns the expected response. Restart the launchd service (label in `scripts/install-service.sh`) so the
  running process matches what was pushed, and only with `lb-restart`:

  ```bash
  ~/.agent-lb/bin/lb-restart --reason "<what>" --from <worktree> --files <runtime-relative paths>
  ~/.agent-lb/bin/lb-restart --reason "<what>"            # files already in the runtime
  ~/.agent-lb/bin/lb-restart --reason "<what>" --check    # boot and health-gate the code on disk only
  ```

  It takes a machine-wide lock, boots the new code as a standby on :2459 and
  health-gates it (on failure it rolls the files back and the primary is never
  touched), points the TCP front at the standby, drains the primary (in-flight
  streams finish, bound 300s) while launchd restarts it, then points the front back.
  New connections never wait. Source: `scripts/lb-restart`; the installed copy is
  `~/.agent-lb/bin/lb-restart`. Never `launchctl kickstart -k` the service by hand: the
  drain bound is 300s and without a standby every new request waits for it (before
  lb-restart, restarts held new connections 45-95s and cut streams past 75s,
  2026-09-25). Plist edits: `lb-restart --reload-plist` (it pauses the watchdog and
  waits for the old job to be gone before bootstrapping). Migrations run while the old
  code still serves, so they must be additive. The front itself is upgraded in place
  with `node scripts/front-hot-swap.mjs` (no dropped connections); never kickstart it.
- **Anthropic request path (`app/core/anthropic/**`, `app/modules/proxy/anthropic*`)**: after the
  restart, run `python3 scripts/claude_cache_eval.py` and keep its receipt. It drives a real Claude
  Code session with a subagent through the proxy and fails if any steady turn rewrote its context.
  Never add, remove or reorder system blocks on a Claude Code payload: its first block is a
  per-request billing marker that Anthropic keeps out of the cache only in first position. Moving it
  broke caching for 43h (2026-09-21) and 12h (2026-09-23), and a partial fix left teammates
  rewriting their whole context for two more days. `clients/cache-watch` (installed by
  `scripts/install-cache-watch.sh`) alerts when the trailing-hour cache-read ratio drops under 90%.
- **Cross-machine**: after pushing, fast-forward every other instance (e.g. the laptop)
  so all checkouts converge on `origin/main`.

The same bar applies to **internal/runtime overrides**: do not merge, push,
restart launchd onto new code, replace the local/internal version, or otherwise
make this checkout the version serving the launchd service (label in
`scripts/install-service.sh`) until the relevant
validation gate has passed. After any server-path override, restart the live
service and exercise the affected endpoint/client path against
`http://127.0.0.1:2455`.

OpenSpec still gates behavior/API/schema changes (see below) — create the change folder
as part of the same validated push, don't skip it.

## Code Conventions

The `/project-conventions` skill is auto-activated on code edits (PreToolUse guard).

| Convention              | Location                              | When                         |
| ----------------------- | ------------------------------------- | ---------------------------- |
| Code Conventions (Full) | `/project-conventions` skill          | On code edit (auto-enforced) |
| Git Workflow            | `.agents/conventions/git-workflow.md` | Commit / PR                  |

## Workflow (OpenSpec-first)

This repo uses **OpenSpec as the primary workflow and SSOT** for change-driven development.

### How to work (default)

1. Find the relevant spec(s) in `openspec/specs/**` and treat them as source-of-truth.
2. If the work changes behavior, requirements, contracts, or schema: create an OpenSpec change in `openspec/changes/**` first (proposal -> tasks).
3. Implement the tasks; keep code + specs in sync (update `spec.md` as needed).
4. Validate specs locally: `openspec validate --specs`
5. When done: verify + archive the change (do not archive unverified changes).

### Source of Truth

- **Specs/Design/Tasks (SSOT)**: `openspec/`
  - Active changes: `openspec/changes/<change>/`
  - Main specs: `openspec/specs/<capability>/spec.md`
  - Archived changes: `openspec/changes/archive/YYYY-MM-DD-<change>/`

## Documentation & Release Notes

- **Do not add/update feature or behavior documentation under `docs/`**. Use OpenSpec context docs under `openspec/specs/<capability>/context.md` (or change-level context under `openspec/changes/<change>/context.md`) as the SSOT.
- **Do not edit `CHANGELOG.md` directly.** Leave changelog updates to the release process; record change notes in OpenSpec artifacts instead.

### Documentation Model (Spec + Context)

- `spec.md` is the **normative SSOT** and should contain only testable requirements.
- Use `openspec/specs/<capability>/context.md` for **free-form context** (purpose, rationale, examples, ops notes).
- If context grows, split into `overview.md`, `rationale.md`, `examples.md`, or `ops.md` within the same capability folder.
- Change-level notes live in `openspec/changes/<change>/context.md` or `notes.md`, then **sync stable context** back into the main context docs.

Prompting cue (use when writing docs):
"Keep `spec.md` strictly for requirements. Add/update `context.md` with purpose, decisions, constraints, failure modes, and at least one concrete example."

### Commands (recommended)

- Start a change: `/opsx:new <kebab-case>`
- Create artifacts (step): `/opsx:continue <change>`
- Create artifacts (fast): `/opsx:ff <change>`
- Implement tasks: `/opsx:apply <change>`
- Verify before archive: `/opsx:verify <change>`
- Sync delta specs → main specs: `/opsx:sync <change>`
- Archive: `/opsx:archive <change>`
- If the local `openspec` executable is missing, use the npm-distributed CLI
  without adding a repo dependency: `npx --yes @fission-ai/openspec@latest validate --specs`.

## Contributing & Merge Gates

When authoring or merging a PR (as a human contributor, a collaborator,
or an AI assistant acting on behalf of either), the binding workflow is
in [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md). The sections
an AI assistant most often needs are:

- [Merge gates](.github/CONTRIBUTING.md#merge-gates) — exact-SHA local CI
  receipt green + owner/coordinator diff review + `mergeable=CLEAN` +
  OpenSpec change folder for behavior changes + `Fixes #N` /
  `Closes #N` for issue cover.
- [Collaborator rules](.github/CONTRIBUTING.md#collaborator-rules) —
  no self-merge by default; large PRs get split (≈1-concern per PR,
  ~800 net lines / scoped capability ceiling).
- [Bus factor escape hatch](.github/CONTRIBUTING.md#bus-factor-escape-hatch)
  — self-merge allowed after **14 days** with all gates met and a
  comment invoking the clause.

An assistant preparing a merge MUST run `python3 scripts/local_ci.py status
<40sha>` for the exact candidate SHA and inspect its receipt with `show`.
The receipt must be from the coordinator machine and have every required tier
green. The assistant must also inspect the actual diff and GitHub mergeability;
hosted CI checks and Codex labels are not merge gates.

## PR Readiness / Review Trapdoors

These rules encode recurring review blockers observed across agent-lb PRs.

- Dashboard, report, and operator-summary account totals should count only
  authenticated, subscription-usable accounts. Unauthenticated, unsubscribed,
  deactivated, or rotation-only stored accounts may remain visible where useful,
  but they must not inflate headline pool/account totals unless the UI/API
  explicitly labels the number as all stored accounts.
- OpenSpec is a hard gate for behavior, API, schema, CLI,
  dashboard-visible, proxy-routing, operator-contract, and compatibility
  changes. Create or update `openspec/changes/<slug>/` before coding, keep
  `spec.md` normative with MUST/SHALL-style requirements, put rationale and
  examples in `context.md` or change notes, and run strict OpenSpec validation
  before calling the PR ready. Code/tests alone are not enough when OpenSpec is
  required.
- Review the current candidate diff directly. A hosted Codex label or cloud
  check is neither required nor a substitute for owner/coordinator review.
- Proxy failover and retry patches must prove account ownership and settlement
  invariants. File-pinned requests must not cross accounts; API-key reservations
  must settle before error-health writes; excluded accounts must actually leave
  the selection loop; idle disconnects must not mark otherwise healthy accounts
  unhealthy; security/trusted-access routing must degrade only along the
  documented path.
- Async, fan-out, and session-lifecycle patches must prove task ownership and
  cleanup. Do not share one `AsyncSession` across concurrent tasks; cancel or
  await spawned tasks on failure; preserve finalization/settlement paths after
  partial errors; bound fan-out; and test partial-failure behavior, not only
  the all-success path.
- Database migrations must prove Alembic graph and data hygiene. New revisions
  must sit on the current intended parent with a single-head upgrade path, have
  downgrade/upgrade coverage where the project expects it, and include
  historical-row backfills or compatibility handling when new fields affect
  existing data.
- Issue-resolving PRs must name the exact `Fixes #N` / `Closes #N`, or state
  that they are partial. Keep PRs one concern wide. Revive stale work by making
  a focused branch on current `main`; do not drag an old broad/conflicted branch
  forward unless the maintainer explicitly wants that shape.
- Bug fixes need regression coverage at the externally failing product path:
  route, bridge, websocket, CLI, schema, dashboard UI, or migration path as
  applicable. Helper-only tests are not enough when the failing surface is
  elsewhere.
- Compatibility work must verify canonical and equivalent paths, trailing slash
  behavior, external error envelopes, env-var semantics, and response-schema
  contracts. Update OpenSpec/context and tests together so docs cannot promise
  behavior the code does not implement.

Coding-agent routing canon: `config/coding-agents/ROUTING.md`.

## Status awareness

Use `agent-lb status --json` or `agent-lb status --provider anthropic --model opus --thinking --json` before planning heavy model work. Quota snapshots are advisory: do not deny agent launches because of a local reserve estimate or missing telemetry. Respect actual provider limits and explicit spending authorization.

## Factory rules for agent-lb (2026-09-26)

agent-lb pushes straight to `main`, so it takes the factory's review rules without a merge
queue. The routing canon (`config/coding-agents/ROUTING.md`) has the evidence; ADR-0003 in
`DECISIONS.md` records the decision.

### The money path, by owned surface

A change is on the money path when it touches any surface below, or anything else that
reads or writes a request body, a credential, account state or a quota mark. The paths are
examples of each surface, not a closed list. When in doubt, it is on the money path.

1. **Request path and payload:** `app/core/anthropic/`, `app/core/openai/`,
   `app/core/clients/`, `app/core/providers/`, `app/core/upstream_proxy/`,
   `app/core/middleware/`, `app/modules/proxy/`, `clients/claude-lb-launch`,
   `clients/codex-lb-launch`. Why: moving the billing block broke the prompt cache for 43 h
   (2026-09-21) and 12 h (2026-09-23). The launchers write aliases, and aliases pick budget
   pools (ADR-0002).
2. **Accounts, credentials and custody:** `app/modules/accounts/`, `app/modules/oauth/`,
   `app/modules/sticky_sessions/`, `app/modules/federation/` (account checkout between
   instances), `app/core/crypto.py`, `scripts/anthropic-auth.sh`, `scripts/openai-auth.sh`.
   Why: the ownership and settlement trapdoors above; a request must not cross accounts.
3. **Auth:** `app/core/auth/`, `app/modules/api_keys/`, `app/modules/dashboard_auth/`,
   `app/modules/firewall/`, and the auth and firewall middleware in `app/core/middleware/`.
   Why: it decides who may call the relay and open the dashboard.
4. **Selector and quota marks:** `app/core/balancer/`, `app/core/usage/`,
   `app/core/rate_limiter/`, `app/modules/usage/`, `app/modules/quota_planner/`,
   `app/modules/pools/`, `app/modules/account_schedule/`, `app/modules/limit_warmup/`.
   Why: they decide which account serves a request, and `route` admits seats from the same
   account marks.
5. **Migrations:** `app/db/alembic/`, `app/db/migrate.py`, `app/db/models.py`. Why: they run
   while the old code still serves, so they must be additive and single-head.
6. **lb-restart and the front:** `scripts/lb-restart`, `scripts/agent-lb-front.mjs`,
   `scripts/front-hot-swap.mjs`, `scripts/install-front.sh`, `scripts/watchdog.sh`,
   `scripts/install-service.sh`, `scripts/sync-runtime.sh`. Why: before lb-restart, restarts
   held new connections for 45 to 95 s (2026-09-25).
7. **Seat routing and the tool boundary:** `clients/route` (admission and auditor choice)
   and `config/coding-agents/hooks/seat-guard.py`. Why: admission and auditor choice decide
   who reviews whom, and the seat guard fails open, so its bugs are silent. The routing
   table and verify-routing are not on the money path; verify-routing is their check.

### Traces: every request names a person and a machine (2026-09-26)

The owner asked that every request show who made it and from which machine, once more than
one person and machine share the relay. Each request log row carries four fields: a person
handle, where the person came from, a machine handle, and where the machine came from.

- **Person** (source `member`, `owner-machine`, `internal` or `unknown`): the handle of the
  member that owns the API key. Any request without a member (keyless, or a key with no
  member) counts as the owner only when its machine is on the local owner-machine list;
  otherwise it is `unknown`. A key's own name is never taken as a person.
- **Machine:** from the client address the auth layer already resolves
  (`resolve_connection_client_ip`, the same trusted-proxy settings as the firewall), never a
  second header parser. Loopback is `local`. A tailnet address takes the node's name from the
  tailnet node cache (`tailnet-unknown` on a miss); the tailnet front overwrites the forwarded
  address, so a peer cannot choose it. Funnel traffic is `funnel`; any other address is
  `remote` and the address is not stored. A loopback request that names its machine in a
  header (an ssh tunnel) is `claimed`.
- **The relay's own work** (warmups, quota probes) is `internal`, never the owner.
- **Handles only.** The owner handle, the owner-machine list and machine aliases are local env
  settings, not part of this public repo. Emails and logins are never stored.
- Anything that reads or writes these fields touches auth and the request path, so it is on
  the money path.

### Review

- **One check per change.** Every change names the command that proves it. A bug fix gets its
  test at the failing product path (the trapdoor above).
- **A fresh verifier from the other vendor reviews every change before push**, and every fix
  round. `verifier` (Opus, high) reviews GPT, Cursor, Devin, GLM and Kimi authors;
  `codex-verifier` (Sol, xhigh) reviews Claude authors. The brief names the author vendor.
  Off the money path one verifier judges the `correct` lens at its own effort.
- **Money path: three lenses at xhigh, and any one FAIL blocks.**
  - `payload`: request fidelity, the billing block stays first, streaming, the cache.
  - `accounts`: credential custody, account ownership on failover, reservations settled
    before health writes, quota marks, admission and auditor choice in `clients/route`.
  - `release`: tests at the failing product path, migrations additive and single-head, the
    lb-restart health gate, the cache eval receipt.
- **At most two fix rounds.** Then split the change or park it, and log the reason.

### The verdict record

Stage everything, then take the diff id:
`git diff --cached | git patch-id --stable | cut -c1-12`. Hand the id to the verifier in its
brief. The verifier echoes it: `VERDICT PASS|FAIL lens=<lens> diff=<id>`. The commit records
the author seat and each verdict as trailers:

```
Seat: gpt-implementer (openai)
Verified-by: verifier (anthropic) PASS lens=payload effort=xhigh diff=3f9c1a2b7d04
Verified-by: verifier (anthropic) PASS lens=accounts effort=xhigh diff=3f9c1a2b7d04
Verified-by: verifier (anthropic) PASS lens=release effort=xhigh diff=3f9c1a2b7d04
```

- Vendors: `anthropic`, `openai`, `cursor`, `devin`, `glm`, `kimi`, `human`.
- Several authors: one `Seat:` line each. Every verifier differs in vendor from all of them.
- Record every verdict in order, FAILs included. The last verdict per lens decides.
- A clean `git pull --rebase` keeps the id; a conflicted one needs a re-verify.
- **Never push after a FAIL.** Fix and re-verify the final diff, or record
  `Verify-override: <reason>` in the commit. An urgent infra fix may go live first with that
  override and get its review within a day.

### Incidents and rule changes

- An incident fix ships its fixture test or check in the same commit. On the factory's
  2026-09-26 night watch a person, not a check, found every incident.
- Rules change with evidence. Record the change as a dated `DECISIONS.md` entry that
  supersedes the old one; history stays in git.

### Why no push check

Nothing checks the trailers; they are the record, not an enforcement. They are
self-attested, so a check could not show that a verifier read the diff. A pre-push hook
would live in the git dir that every worktree and the live main checkout share, so
installing it changes other agents' pushes. A self-attested check stops only an accidental
push. The verifier brief and this rule carry the weight. A guard may come later, when a hook
can be installed per worktree.
