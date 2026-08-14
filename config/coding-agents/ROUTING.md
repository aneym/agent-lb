# Canonical coding-agent modes

This file is the single source of truth for coding-agent model routing on this
computer. Its stable host-neutral path is
`~/.agents/policy/coding-agents/ROUTING.md`. Host instructions and skills are
adapters: when they disagree with this file, this file wins.

## Northstar (owner, 2026-07-15)

Optimize for keeping Claude capacity safe without sacrificing intelligence —
**hands vs brain**. Claude weekly capacity is a hard scarce resource; the brain
decides, hands do volume. One capable driver, one adversarial partner,
cheap fast fan-out. Opus 5 drives (orchestrates, decides,
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

Claude Code, entered through `cc`, is the only coding harness. Opus 5/high with
the 1M context variant is the normal driver. The launcher defaults to
`claude-opus-5[1m]` and a 1M autocompact window; explicit model or autocompact
flags still win. Fable remains an explicit brain-work driver and currently has
a verified 200k model context, even though its compaction ceiling is configured
at 1M for forward compatibility. Opus 5/high is
the driver: it coordinates, decides, reconciles, and verifies using the native
harness — `Agent`/`Workflow` subagents, skills, and hooks.

The canonical seat lineup (owner, 2026-07-15) is fixed per SEAT, not chosen
per task. Sol seats are served by agent-lb's Messages-route model aliases
(commit 5c173b29):

| Seat                   | Agent definition                        | Model                | Effort            |
| ---------------------- | --------------------------------------- | -------------------- | ----------------- |
| Driver (main loop)     | —                                       | `claude-opus-5`      | high              |
| Explore / scouts       | `~/.claude/agents/Explore.md`           | `gpt-5.6-sol-medium` | medium, fast tier |
| Implementer            | `~/.claude/agents/implementer.md`       | `gpt-5.6-sol-medium` | medium, fast tier |
| Verifier (adversarial) | `~/.claude/agents/verifier.md`          | `gpt-5.6-sol-xhigh`  | xhigh, fast tier  |
| Copywriter             | `~/.claude/agents/copywriter.md`        | `gpt-5.6-sol-xhigh`  | xhigh, fast tier  |
| Frontend designer      | `~/.claude/agents/frontend-designer.md` | `claude-opus-5`      | inherit high      |
| Planner (lane lead)    | `~/.claude/agents/planner.md`           | `claude-planner`     | high; Fable 5 primary, Opus 5 on scoped exhaustion |
| Plan reviewer          | `~/.claude/agents/plan-reviewer.md`     | `claude-planner`       | high; Fable 5 primary, Opus 5 on scoped exhaustion |

Session lifecycle: finish and verify one coherent unit, write or return its
bounded handoff, then terminate that worker before starting a materially new
unit. Rotate at clean boundaries, not mid-edit, mid-test, mid-merge, mid-deploy,
or while a consequential decision is unresolved. Fresh sessions are the
default between phases; `/resume` is for genuinely continuous work.

Explore moved sonnet → gpt-5.6-sol-medium (owner, 2026-07-15 evening):
benchmarked 3/3 repo-exploration accuracy matching sonnet at 3.1x speed with
61% fewer calls (artifacts: /tmp/agent-lb-explore-benchmark-20260715), and a
production-shaped Fable→sol Explore run verified via session analytics.
Also relieves the Claude pool — sonnet Explore fan-outs were the first
casualties of pool saturation (live-watch 21:15Z: scout failures on
rate-limited sonnet). Caveat on record: n=3 benchmark; revisit if Explore
quality regresses.

Copywriter seat added (owner, 2026-08-03): `gpt-5.6-sol-xhigh` for marketing
and product copy — owner judges 5.6 the stronger copywriter (less verbose, no
private-language drift). The driver still writes conversational and technical
prose; user-facing marketing copy dispatches to this seat. Carries the Orwell
writing system from global CLAUDE.md in its agent definition.

The frontend-designer seat is the sanctioned expensive exception (rule 3):
design taste is capability-bound, so it runs on Opus — but it has no
Edit/Write tools, produces only specs and ranked crits for the implementer
seat, and stays low-volume/high-leverage (added 2026-07-15 after fleet audits
showed UI sessions burning the most driver capacity on taste-then-pixels
loops).

The planner seat (2026-07-24) is the sanctioned Fable-primary TEAMMATE: a lane
coordinator that plans, dispatches its own canonical seats, and reconciles —
the brain of a delegated workstream (loop lanes, multi-seat sub-projects). Its
`claude-planner` alias resolves to Fable 5 unless every otherwise-routable
Anthropic account has a fresh, future-reset Fable-scoped exhaustion marker;
only then does it resolve to Opus 5. Soft weekly thresholds, partial or stale
markers, generic rate limits, auth/network/529 failures, and total Anthropic
exhaustion do not trigger the fallback.
Teammate enforcement fact (verified): hooks and CLAUDE.md context do NOT
reach spawned teammates — 0 hook executions across 7 teammate transcripts vs
14 in their coordinator. Therefore: (a) economics are enforced at the SPAWN
boundary (seat-guard runs in the spawning session); (b) rules for teammates
ride in their agent definitions, and the planner's definition orders it to
read ROUTING.md as its first action; (c) teammate models bind from
agent-registry frontmatter at spawn, so the registry is the control point —
never RUN.md prose. One planner per lane; planners never spawn planners.

The plan-reviewer seat (2026-07-25) is the second sanctioned Fable seat, on
the same `claude-planner` alias as the planner: judging a plan is the same
brain work as writing one, so it is capability-bound, not volume. It reviews
plans/PRDs/specs before implementation dispatch — fact-checking every repo
claim the plan makes against the actual codebase, then judging assumptions,
acceptance criteria, decomposition (against the Fan-out doctrine below), and
blast radius. It has no Edit/Write and never rewrites the plan; it returns a
bounded verdict with `file:line` evidence, and the coordinator decides what
changes. Findings without evidence do not count. Fact-checking the plan
against the repo is the reason the seat exists: an internally coherent plan
that is wrong about the codebase is the most expensive failure mode.

Ad-hoc model switching outside these seats stays forbidden — no Codex-host
dispatch, Composer, Gemini, or other model products as coding lanes, and no
per-task improvisation of the lineup. Catch-all subagents
(`general-purpose`/default) must pin a cheap model (`sonnet`/`haiku`)
explicitly; inheriting the expensive driver model is hook-denied (see
Runtime enforcement). `fork` is exempt — context-carrying offload
(long doc/plan generation with full conversation context) is a legitimate
Opus lane. Changing the lineup means editing this table (and the agent
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

## Kimi mode (owner, 2026-07-24)

`kimi` is a driver-swap of the SAME harness and the SAME seat lineup, not a
separate stack. It runs Claude Code through agent-lb like `cc`; only the
driver and the Opus/Fable default slots remap to `kimi-k3`. The sonnet,
haiku, and subagent slots are deliberately left unset so Explore/implementer
(`gpt-5.6-sol-medium`), verifier (`gpt-5.6-sol-xhigh`), and sonnet catch-alls
keep resolving to their own pools through the LB. Verified live 2026-07-24:
one kimi-mode session logged driver `kimi-k3` + Explore `gpt-5.6-sol` +
`claude-sonnet-5` in the same run.

The old `kimi` function pointed straight at Moonshot and flattened opus,
sonnet, haiku, AND subagent to one Kimi model — that silently destroyed the
lineup, since sol-alias seats cannot be served when the base URL is not the
LB. Never reintroduce that shape: a provider swap that also overrides the
seat slots is a lineup change, and lineup changes mean editing this file.

Kimi accounts are pooled by agent-lb like any other provider (change
`add-kimi-provider`); credentials live in the LB, never in the launcher env.

## Fable driver mode (owner, 2026-07-25)

`fable` is a driver-swap of the SAME harness and the SAME seat lineup, in the
shape sanctioned for `kimi`. It runs Claude Code through agent-lb like `cc`
(`clients/fable` execs `claude-lb-launch`, so LB probing, limit waiting, the
banner, and session ids are identical); only the driver and the opus/fable
default slots resolve to `claude-fable-5`. The sonnet, haiku, and subagent
slots are deliberately left unset so Explore/implementer
(`gpt-5.6-sol-medium`), verifier (`gpt-5.6-sol-xhigh`), and sonnet catch-alls
keep resolving to their own pools through the LB. `AGENT_LB_FABLE_MODEL`
overrides the driver model; an explicit `--model` still wins.

Planning in this mode is Fable end to end without pinning anything new: the
driver itself is Fable, Claude Code's built-in plan agent inherits the driver,
and the planner and plan-reviewer seats already resolve Fable-first through
`claude-planner`.

This mode spends the scarce Claude pool by design — it is for sessions whose
work IS brain work (architecture, spec/canon authoring, plan review), not a
default. The hands-vs-brain contract does not relax inside it: volume work
still goes to the sol seats, and seat-guard still denies expensive ad-hoc
dispatches. A `fable` session that fans out to sol seats is the intended
shape; one that reads forty files itself is the leak the pulse hook exists to
catch.

Never let this become a slot flattening. The retired `kimi` shape — one model
across opus, sonnet, haiku, AND subagent — is exactly what destroys the
lineup; a driver swap that also overrides the seat slots is a lineup change,
and lineup changes mean editing this file.

## Fan-out doctrine (owner, 2026-07-17)

Fan-out is the DEFAULT, not an optimization the driver reaches for when
reminded. When work decomposes into 2+ independent sub-units, dispatch them
concurrently — multiple seat dispatches in one message, or a Workflow script
when the orchestration is deterministic (pipelines, verify fan-out, loops;
standing opt-in + model rules: Workflow orchestration section below).
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

## Workflow orchestration (owner standing opt-in, 2026-08-01)

This section is the owner's explicit, durable request to use the Claude Code
`Workflow` tool: any session governed by this file may run workflows without
a per-prompt ask whenever the Fan-out doctrine applies — 2+ independent
sub-units whose orchestration is deterministic (pipelines, per-unit verify
fan-out, discovery loops, migration sweeps). Prefer a Workflow script over
hand-serialized Agent dispatches when the control flow is loops, conditionals,
or fan-out; prefer plain seat dispatches when it is one or two seats, or when
the next step depends on driver judgment between units.

Model rules do NOT relax inside a script:

- Every `agent()` call MUST pin a canonical seat via `opts.agentType`
  (`Explore`, `implementer`, `verifier`; frontend-designer/planner only per
  their seat rules above). A bare `agent()` inherits the DRIVER model — the
  exact leak this file exists to prevent — and seat-guard cannot see inside
  Workflow scripts, so this rule is the control point, enforced at script
  authoring time.
- Never pass `opts.model`; seat models bind from agent-registry frontmatter.
  `opts.effort` may only go DOWN (e.g. `low` for mechanical stages), never up
  past the seat's table effort.
- Shared-contract law still applies: parallel implementer stages get the
  frozen contract verbatim in their prompts, or they don't fan out.
- Respect the session's workflow size guideline (default medium, <15 agents)
  unless the task genuinely calls for more; `log()` any coverage a cap drops.
- Closeout discipline holds: scripts return bounded structured results and
  the driver still independently verifies acceptance — a workflow's green
  return is a claim, not proof.

→ Prevents: decomposable work grinding serially for speed reasons (the
workflow IS the fast path), and workflow scripts silently burning
driver-model tokens on volume stages (the economics leak seat-guard cannot
catch inside scripts).

## Dispatch hygiene (owner-directed session audit, 2026-08-04)

Findings from a full-session audit of the Buzz/Hermes overnight build. Each
rule names the mistake it prevents; all evidence is from that session.

1. **Preflight the brief's load-bearing environment.** Before dispatching a
   seat whose plan depends on external state — a daemon, disk headroom for a
   build, an unlocked GUI session, a TCC grant — the driver verifies that
   state (seconds) and writes the verified facts into the brief.
   → Prevents: a validation seat briefed onto a dead-on-arrival plan (Docker
   was down; 14 GB disk for a multi-GB Rust build), and a full AX GUI driver
   built before anyone checked that the screen was locked.
2. **Two failed hypotheses → hand the repro to a seat.** Empirical debugging
   (bisection, wire captures, env archaeology) leaves the driver after the
   second falsified hypothesis: freeze the repro command and observations
   into a brief and dispatch. Standing first check for any local HTTP
   anomaly: `env | grep -i proxy` before any capture tooling.
   → Prevents: the driver burning ~15 calls tracing an "HTTP 501" that was
   `HTTPS_PROXY` in its own shell.
3. **Fix cycles dispatch on complete verdicts.** When a verifier is
   mid-report, wait for its bounded closeout before briefing the fix cycle;
   only a stop-ship blocker justifies acting on a partial. Batch all known
   scope — defects, standards, restructuring — into one fix brief.
   → Prevents: three serial instruction batches to one implementer,
   including an amend that later restructuring partially discarded.
4. **Background seats carry a liveness deadline.** State the expected
   closeout window in the brief; when the coordinator goes idle with seats
   in flight, arm a watchdog (Monitor or a delayed background job). Silence
   past the window = one ping, then respawn with the same brief.
   → Prevents: a verifier sitting nine hours unreported overnight while the
   coordinator waited.
5. **Local knowledge before external research.** Research briefs begin with
   a recall pass over local knowledge surfaces (agent memory, nest
   RESEARCH/GUIDES, profile skills) and hand hits to seats as starting
   context.
   → Prevents: re-deriving a Buzz↔Hermes integration case study that had
   existed in a profile's skills since July, found late by accident.

Counter-evidence worth keeping: the implementer→verifier adversarial loop is
not overhead — in the audited session it caught a key-destroying defect and
a validation gap sitting behind 322 green tests and a clean clippy. Speed
comes from the five rules above, never from skipping the verify pass on
work that outlives the session.

## Operating contract

1. One harness, one coordinator. Opus owns the user conversation,
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

- `clients/claude-lb-launch` defaults `cc` sessions to Opus 5/high.
- `clients/fable` execs the same launcher with the driver and opus/fable
  slots set to `claude-fable-5` and the seat slots untouched.
- `scripts/install-claude-clients.sh` installs this policy and the `cc` and
  `fable` clients, and removes retired ccdex artifacts (clients, hook, MCP
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
live `cc` response reporting Opus 5/high, or an explicit current provider-capacity
blocker.
