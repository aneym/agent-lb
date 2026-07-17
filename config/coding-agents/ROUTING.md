# Canonical coding-agent modes

This file is the single source of truth for coding-agent model routing on this
computer. Its stable host-neutral path is
`~/.agents/policy/coding-agents/ROUTING.md`. Host instructions and skills are
adapters: when they disagree with this file, this file wins.

## Northstar (owner, 2026-07-15)

Optimize for keeping Fable usage safe without sacrificing intelligence —
**hands vs brain**. Fable weekly capacity is a hard scarce resource; the brain
decides, hands do volume. One expensive driver, one adversarial partner,
cheap fast fan-out. Fable-level intelligence drives (orchestrates, decides,
verifies) while spending as few of its own tokens as possible: subagents and
subagent workflows do the volume work quickly and in parallel.

Feedback loop (2026-07-15): every session's own numbers are live in the
agent-lb session map (`/api/sessions`, status-line `/s/<id>` link); a global
UserPromptSubmit hook (`~/.claude/hooks/routing-pulse.py`) injects a nudge
when a session's driver-model request share grows without seat dispatches.
Fleet observation showed policy prose alone does not hold — the pulse hook is
the enforcement arm. Manual correction: invoke `/route-fix` in any session
(or `/route-fix <session-id | /s/ link>` to diagnose another) — audits the
session against its own LB numbers and re-dispatches in-flight volume work
to the canonical seats (`~/.claude/skills/route-fix/SKILL.md`). GPT Sol at xhigh effort is the adversarial
review subagent — the driver and it jam to decide direction on substantive
calls. The alias registry exists to serve this shape; judge every routing
change against it.

## Mode: raw Claude Code harness with canonical seats (2026-07-15)

Claude Code, entered through `cc`, is the only coding harness. Fable/high is
the driver: it coordinates, decides, reconciles, and verifies using the native
harness — `Agent`/`Workflow` subagents, skills, and hooks.

The canonical seat lineup (owner, 2026-07-15) is fixed per SEAT, not chosen
per task. Sol seats are served by agent-lb's Messages-route model aliases
(commit 5c173b29):

| Seat                   | Agent definition                        | Model                | Effort            |
| ---------------------- | --------------------------------------- | -------------------- | ----------------- |
| Driver (main loop)     | —                                       | `claude-fable-5`     | high              |
| Explore / scouts       | `~/.claude/agents/Explore.md`           | `gpt-5.6-sol-medium` | medium, fast tier |
| Implementer            | `~/.claude/agents/implementer.md`       | `gpt-5.6-sol-medium` | medium, fast tier |
| Verifier (adversarial) | `~/.claude/agents/verifier.md`          | `gpt-5.6-sol-xhigh`  | xhigh, fast tier  |
| Frontend designer      | `~/.claude/agents/frontend-designer.md` | `claude-opus-4-8`    | inherit           |
| Planner (lane lead)    | `~/.claude/agents/planner.md`           | `claude-fable-5`     | inherit           |

Explore moved sonnet → gpt-5.6-sol-medium (owner, 2026-07-15 evening):
benchmarked 3/3 repo-exploration accuracy matching sonnet at 3.1x speed with
61% fewer calls (artifacts: /tmp/agent-lb-explore-benchmark-20260715), and a
production-shaped Fable→sol Explore run verified via session analytics.
Also relieves the Claude pool — sonnet Explore fan-outs were the first
casualties of pool saturation (live-watch 21:15Z: scout failures on
rate-limited sonnet). Caveat on record: n=3 benchmark; revisit if Explore
quality regresses.

The frontend-designer seat is the sanctioned expensive exception (rule 3):
design taste is capability-bound, so it runs on Opus — but it has no
Edit/Write tools, produces only specs and ranked crits for the implementer
seat, and stays low-volume/high-leverage (added 2026-07-15 after fleet audits
showed UI sessions burning the most driver capacity on taste-then-pixels
loops).

The planner seat (2026-07-15) is the sanctioned Fable TEAMMATE: a lane
coordinator that plans, dispatches its own canonical seats, and reconciles —
the brain of a delegated workstream (loop lanes, multi-seat sub-projects).
Teammate enforcement fact (verified): hooks and CLAUDE.md context do NOT
reach spawned teammates — 0 hook executions across 7 teammate transcripts vs
14 in their coordinator. Therefore: (a) economics are enforced at the SPAWN
boundary (seat-guard runs in the spawning session); (b) rules for teammates
ride in their agent definitions, and the planner's definition orders it to
read ROUTING.md as its first action; (c) teammate models bind from
agent-registry frontmatter at spawn, so the registry is the control point —
never RUN.md prose. One planner per lane; planners never spawn planners.

Ad-hoc model switching outside these seats stays forbidden — no Codex-host
dispatch, Composer, Gemini, or other model products as coding lanes, and no
per-task improvisation of the lineup. Catch-all subagents
(`general-purpose`/default) must pin a cheap model (`sonnet`/`haiku`)
explicitly; inheriting the expensive driver model is hook-denied (see
Runtime enforcement). `fork` is exempt — context-carrying offload
(long doc/plan generation with full conversation context) is a legitimate
Fable lane. Changing the lineup means editing this table (and the agent
files), not overriding it in a session.

Driver scope of WORK (not just tool calls): the driver keeps brain work —
decisions, architecture, spec/canon authoring, and quality artifacts that
need full context (plan HTMLs, PRDs, design docs). It hands off volume and
mechanical work. Parallel fan-out is encouraged: dispatch independent seats
concurrently rather than serially; the guard gates economics, never
concurrency. Observed 2026-07-15: identical delegated work cost ~13x more on
ad-hoc opus teammates than on the canonical sol bridge seats — and burned
the scarce Claude pool instead of the Codex pool.

The Codex dispatch stack remains retired (2026-07-15): the `ccdex` entry
point, the codex skills and plugin, the `ccdex-worker` MCP transport, and the
`ccdex-gpt-only` hook. Sol seats run INSIDE the Claude Code harness via the
alias bridge, not through a second harness.

## Fan-out doctrine (owner, 2026-07-17)

Fan-out is the DEFAULT, not an optimization the driver reaches for when
reminded. When work decomposes into 2+ independent sub-units, dispatch them
concurrently — multiple seat dispatches in one message, or a Workflow script
when the orchestration is deterministic (pipelines, verify fan-out, loops).
A brief that hands one seat a multi-unit pipeline is the DISPATCHER'S bug:
split before spawning.

What makes parallel implementation safe is a **shared contract**: before any
implementation fan-out, the driver freezes the interface — types, schemas,
function signatures, file ownership per lane, acceptance checks — and writes
it verbatim into every brief. Lanes build against the contract, never against
each other's in-flight work. No contract → no implementation fan-out (N
incompatible halves is worse than serial).

Serialize only real constraints: a consumer that needs a producer's LANDED
artifact, shared-file clusters (chain into one worktree/lane), or shared
mutable infrastructure (one browser driver, one tsc, one live deployment).
Verification rides in parallel too — independent verify per unit as it lands,
no barrier on sibling units.

→ Prevents: decomposable work grinding through one serial seat (2026-07-17
owner correction: a 3-part Hermes-VPS unit with two independent halves went
to a single implementer).

## Operating contract

1. One harness, one coordinator. Fable owns the user conversation,
   decomposition, dispatch, reconciliation, and final verification.
2. Delegated subagents return a bounded closeout: conclusion, evidence,
   verification, next action, and artifact paths. The coordinator
   independently checks the acceptance criteria.
3. Subagent models are pinned by the canonical seat table above, in the agent
   definitions themselves. Any other model override is an exception that must
   state its cost or capability reason in the definition.
4. Team messaging discipline (2026-07-15, relaxed same day: teams
   intercommunicating is valuable — owner). Teammates MAY message each
   other, with addressing discipline targeting the two observed failure
   modes (misrouted pings from guessed names; resume/idle notification
   storms from pinging finished agents):
   - Address only teammates NAMED IN YOUR BRIEF or that messaged you first.
     Never guess a name — resolution is latest-wins with no directory; a
     wrong guess delivers your question to a stranger.
   - Never ping a finished/idle agent just to confirm or thank — every send
     resumes it and fires fresh notifications. No response after ONE follow-up
     → escalate to the coordinator; never retry-storm a peer.
   - Peer chat exchanges data and evidence. Scope changes, conflicts, and
     cross-lane decisions go to the coordinator, who remains the authority.
   - Closeouts still go to the coordinator — peer messages never substitute
     for the bounded closeout.
5. Driver scope (2026-07-15). The driver's own tool calls are for
   coordination only: reading a subagent's cited evidence to check it,
   spot-reading a handful of lines before a decision, and running the final
   acceptance checks. Everything else — multi-file reading, format/pattern
   discovery, investigation loops, harness/debug scripts, anything empirical
   that can fail and be retried — is seat work, even when each step looks
   one-call-sized. Bright lines: more than ~3 direct reads on the same
   question, or ANY second attempt at a failed empirical step, means stop and
   dispatch (Explore for read-only questions, implementer for
   build-run-report). Drift erodes one "quick check" at a time; count calls,
   not intentions.
6. Fan out on a shared contract (owner, 2026-07-17). Independent sub-units
   dispatch concurrently against a driver-frozen contract; one-seat serial
   pipelines over decomposable work are a dispatch bug. Full text: the
   Fan-out doctrine section above.

## Runtime enforcement

- `clients/claude-lb-launch` defaults `cc` sessions to Fable/high.
- `scripts/install-claude-clients.sh` installs this policy and the `cc`
  client, and removes retired ccdex artifacts (clients, hook, MCP
  registration) when it finds them.
- `~/.claude/hooks/seat-guard.py` (global PreToolUse on Agent): denies
  expensive ad-hoc dispatches — explicit fable/opus overrides, or catch-all
  subagent types inheriting the driver model — with a corrective reason
  naming the seats. Fail-open, <30ms, no I/O.
- `~/.claude/hooks/routing-pulse.py` (global UserPromptSubmit): injects the
  session's own driver-vs-seat numbers from the agent-lb session map when
  the ratio degrades; throttled 15min, silent when healthy or LB down.
- `/route-fix` skill: manual audit + live re-routing for any session by id
  or `/s/` link.

## Validation

Run `~/.agents/policy/coding-agents/verify-routing` for deterministic machine
checks. Routing is not proven by prose alone. A valid rollout also has one
live `cc` response reporting Fable, or an explicit current provider-capacity
blocker.
