# agent-lb agent rules: the long form

`AGENTS.md` holds the short rules every session loads (Alex, 2026-09-25: short, loose
instruction files; detail lives in pages agents read when needed). This page is the
full text it was cut from. If they disagree, AGENTS.md wins; fix this page. The
"Coding-agent routing" block below is retired (it named Fable as driver); the canon is
`config/coding-agents/ROUTING.md`.

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

**Development on this fork (`aneym/agent-lb`) stays on `main`.** Work directly on `main`; do not create or switch to feature branches unless the user explicitly asks. `feat/anthropic-provider` was consolidated into `main` on 2026-06-09 and is retired. The "do not commit directly to main" convention in `.agents/conventions/git-workflow.md` applies only to upstream (`Soju06/codex-lb`) contributions. The local checkout also runs the live launchd service (`com.aneyman.agent-lb`), so the working tree must remain on `main`.

## Auto-publish (standing authorization)

Validated changes ship immediately — no per-change confirmation. After a change
passes its **validation gate**, commit directly to `main` and `git push origin main`
right away. This is a standing instruction from the repo owner.

"Validated" means the change was actually **exercised**, not merely asserted:

- **Launcher / client (`clients/**`)**: byte-compiles (`python -m py_compile`) and a
  `CLAUDE_LB_DRY_RUN=1` (or real) round-trip behaves correctly.
- **Server (`app/**`)**: imports clean, `ruff check app clients` passes, the relevant
  tests pass, and — for runtime behavior — the service starts and the affected endpoint
  returns the expected response. Restart the live `com.aneyman.agent-lb` service so the
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
make this checkout the version serving `com.aneyman.agent-lb` until the relevant
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

<!-- routing:begin — synced pointer; canon lives in ~/.agents/policy/coding-agents/ROUTING.md -->

## Coding-agent routing (global canon — read this)

Hands vs brain: the driver (Fable) decides, architects, and writes
full-context artifacts; ALL volume work (multi-file reads, mechanical edits,
retries, builds) is dispatched to seats — Explore (read-only), implementer
(build-run-report), verifier (adversarial), frontend-designer (UI direction).
>~3 direct reads on one question or ANY retry of a failed step → dispatch a
seat. Canon + enforcement: `~/.agents/policy/coding-agents/ROUTING.md`. Your
session's live routing numbers are behind the status-line link
(`http://127.0.0.1:2455/s/<session-prefix>`).

<!-- routing:end -->

## Status awareness

Use `agent-lb status --json` or `agent-lb status --provider anthropic --model claude-fable-5-1 --thinking --json` before planning heavy model work. Quota snapshots are advisory: do not deny agent launches because of a local reserve estimate or missing telemetry. Respect actual provider limits and explicit spending authorization.
