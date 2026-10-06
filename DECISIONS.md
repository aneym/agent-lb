# Architectural Decisions

This file records long-lived architecture decisions for agent-lb. New decisions
are appended and superseded by later entries rather than edited in place.

## ADR-0001: ProxyService target-architecture cutover refactor

- **Date:** 2026-06-05
- **Status:** Accepted
- **Scope:** `app/modules/proxy/service.py` and proxy runtime internals

### Context

`app/modules/proxy/service.py` grew into a large god object that mixed HTTP
bridge orchestration, WebSocket proxying, streaming retry and settlement,
request logging, API-key usage, file operations, compact responses, warmup,
rate-limit payloads, and observability helpers in one `ProxyService` class.

The service is production-critical and contains compatibility behavior that is
hard to safely rewrite in one pass. A big-bang replacement would create high
review risk, hidden behavior drift, and poor rollback properties. Random small
file-shaving also fails to create durable boundaries and lets the façade grow
again during later feature work.

### Decision

Use a **target-architecture cutover refactor** for proxy service decomposition.

`app/modules/proxy/service.py` remains the stable public façade and compatibility
import surface. Extracted implementation lives behind the private package
`app/modules/proxy/_service/`.

Small cohesive domains are leaf modules under `_service/`. Large domains are
folder packages with their own internal structure:

```text
app/modules/proxy/
├── service.py                    # public façade / compatibility surface
├── _support.py                   # compatibility shim
├── _warmup.py                    # compatibility shim
└── _service/                     # private implementation package
    ├── support.py
    ├── warmup.py
    ├── api_key_usage.py
    ├── request_log.py
    ├── rate_limit.py
    ├── file_ops.py
    ├── transcribe.py
    ├── codex_control.py
    ├── compact.py
    ├── observability.py
    ├── http_bridge/              # future: session lifecycle, relay, retry
    ├── websocket/                # future: connect, request state, replay
    ├── streaming/                # future: retry, once, settlement
    └── account_selection/        # future: budget, failover, admission
```

For high-risk future domains such as HTTP bridge, WebSocket, streaming, and
account selection, use this sequence:

1. Add characterization/golden-master tests for the current behavior
2. Use Mikado-style discovery to map hidden couplings and prerequisites
3. Introduce narrow protocols/adapters where needed, following Branch by
   Abstraction inside the monolith
4. Build the target domain package behind the façade
5. Switch `ProxyService` to the new package in one controlled cutover
6. Keep the codebase releasable at each commit

Architecture fitness functions are part of the decision. They ratchet the
current decomposition direction and prevent accidental regression:

- `service.py` must stay below the accepted line-count threshold
- `ProxyService` maximum method span must not grow beyond the accepted threshold
- `_support.py` and `_warmup.py` must remain compatibility shims only
- `service.py` must preserve required façade re-exports for existing consumers
- `_service/*` modules must not form arbitrary cross-domain dependencies

### Alternatives considered

| Alternative | Why rejected |
| --- | --- |
| Big-bang rewrite of `ProxyService` | Too much behavior would move at once; difficult to review, test, and roll back |
| Continue small ad-hoc extraction PRs | Reduces line count but does not create durable domain ownership or prevent regression |
| Split into external microservices now | Deployment/runtime complexity is unnecessary for the current problem; this is an internal modularity issue first |
| Keep `service.py` as the implementation owner indefinitely | Preserves compatibility but leaves the god-object failure mode in place |

### Consequences

- Future proxy work should add new domain behavior under `_service/`, not directly
  into `service.py`
- Compatibility shims may remain while internal consumers migrate, but they must
  not gain new behavior
- Large domain migrations should prefer complete package cutovers with targeted
  characterization tests over shallow helper movement
- CI can enforce architectural ratchets through `scripts/check_proxy_architecture.py`
- The accepted thresholds are starting ratchets, not final goals; future PRs
  should lower them after major domain packages are extracted

## ADR-0002: Keep driver aliases separate from canonical model seats

- **Date:** 2026-09-13
- **Status:** Accepted
- **Scope:** Coding-agent launchers and doctor diagnostics

### Context

Provider aliases are not interchangeable labels. They select distinct budget
pools, while canonical model seats describe the role a model must fill. Writing
a driver alias into every model slot can silently move an Opus seat onto the
Fable budget and corrupt the intended lineup.

Claude Code creates and caches its session agent registry. If doctor does not
validate the configured aliases first, an invalid routing setup can be cached
for the session lifetime.

### Decision

- A driver launcher owns only its driver slots. The Fable launcher owns
  `ANTHROPIC_MODEL` and `ANTHROPIC_DEFAULT_FABLE_MODEL`.
- Canonical seats are resolved independently. The Fable launcher always writes
  `ANTHROPIC_DEFAULT_OPUS_MODEL` from a nonempty `AGENT_LB_OPUS_MODEL`, falling
  back to `claude-opus-5`.
- Sonnet, Haiku, and subagent seat slots remain outside the Fable driver swap.
- Doctor validates model aliases and canonical-seat assignments before Claude
  Code creates and caches its session agent registry.

### Consequences

- An inherited Fable value cannot poison the canonical Opus slot.
- Driver changes do not implicitly become lineup changes.
- Source inspection and unit tests establish configuration behavior only; they
  do not constitute a live routing or provider-budget claim.

## ADR-0003: The factory principles govern the routing canon and agent-lb's own changes

- **Date:** 2026-09-26
- **Status:** Accepted
- **Scope:** `config/coding-agents/**` and agent-lb's push flow

### Context

The routing canon lagged the factory decisions of 25 and 26 Sep. It still called
cross-vendor review optional and started the implement ladder at Luna. Meanwhile
orch-lab measured the alternatives (E5, E6, E7, E10), and on 2026-09-25 a change
queued after its verifier failed it crash-looped production from 22:59Z to 23:07Z.
The rules lived in one machine's local `models.md`, so other machines never
received them.

### Decision

- The factory principles are canon in `config/coding-agents/ROUTING.md` and
  `routing-table.json`: Opus plans, specs and verifies, with a `sol-consult`
  second opinion on complex or risky plans; `gpt-implementer` (Sol, medium)
  writes all code to a spec with one check; `luna-implementer` and
  `sonnet-implementer` are off the default path; a fresh verifier from the
  other vendor reviews every unit and fix round; money-path units get three
  lenses at xhigh where any FAIL blocks; a verdict is bound to its diff and
  nothing ships after a FAIL without a re-verify or a logged override; at most
  two fix rounds; incident fixes ship a check; effort is fixed per stage; lanes
  run as workflows with `agentType` on every `agent()` call; fan out until a
  real limit; rules change with evidence.
- agent-lb's own changes follow the same review rules. Its money path is
  defined by owned surface (AGENTS.md rule 8, the long form lists the surfaces),
  its lenses are payload, accounts and release, and the verdict record is a set
  of `Seat:` and `Verified-by:` commit trailers carrying the diff id.
- The canon states the router's measured fallback, not an aspiration:
  `route pick implement` moves to `opus-seat` only when it skips
  `gpt-implementer` and the Anthropic pool is on pace and above low and the
  Codex pool is above critical. A low Codex pool does not move implementation
  to Opus.

### Evidence

All small n.

- E5 (5 units): an end-to-end-only check passed a unit with a real regression;
  existing tests plus an implementer-written test caught it.
- E6 (6 units per seat): Sol 6/6 at about 105k tokens per accepted unit, Luna
  5/6 at about 135k, Sonnet 5/6 at about 284k, Opus 4/6 at about 618k.
- E7 (8 candidates, 5 planted bugs): three lenses any-fail 5/5; fresh Sol xhigh
  4/5; fresh Opus high 4/5; majority vote 3/5; one standing context 3/5; both
  single reviewers missed a missing version bump.
- E10 (the E6 units): Sol medium 6/6 at 92k tokens per accepted unit in
  14.0 min; Sol low 5/6 at 124k in 19.1 min.
- Implement admission, measured 2026-09-26T20:15Z with `route pick implement`
  against the source table and fixture pools (Anthropic pool on pace):

  | Codex pool | gpt-implementer up | gpt-implementer recorded down |
  |---|---|---|
  | ok | gpt-implementer | opus-seat, audited by codex-verifier |
  | low | gpt-implementer | opus-seat |
  | critical | gpt-implementer | nothing routable |
  | exhausted | nothing routable | not run |

### Alternatives

1. A pre-push trailer check with a money-path glob map. Rejected for now: this
   change would not install it, the hooks dir is shared with the live main
   checkout, and self-attested trailers cannot prove a review happened.
2. `scripts/local_ci.py` as the gate. Rejected: a direct push never runs it,
   and collaborator PRs carry no seat trailers.
3. Keep the local `models.md` authoritative. Rejected: it is local, so other
   machines never received the rules.
4. Make `route` skip `gpt-implementer` on a low Codex pool. Deferred: it
   changes live routing and the Open Factory eval arms, so it needs its own
   OpenSpec change and a replay first.

### Consequences

- Open Factory's static and Jev menus change with the new chains.
- Every machine reinstalls the policy; three GPT seats become managed.
- Money-path pushes cost three xhigh lenses.
- Nothing enforces the push rule; a push without review is caught only by the
  next reviewer or an incident.

### How it changes

A new entry with new evidence supersedes this one. This entry is not edited.

## ADR-0004: Seats, their boxes and the implement escalation are routing data resolved at a pinned commit

- **Date:** 2026-09-27
- **Status:** Accepted
- **Scope:** config/coding-agents/**, clients/route

### Context

The owner's 2026-09-27 steer: "sol wont be the only implementer option, we just have our sync'd routing rules. so that when we update them, all machines get them". Four seats existed on one machine only, and workflow templates hardcode seat sets.

### Decision

Every definition has one seat entry naming its vendor, box adapter or local-only status, and (for seats without class entries) its model alias and effort. Auditors carry `needs_test_run`. The implement escalation seat and fix round are routing data. The installer manages all definitions and the verifier checks their consistency. `route seat` and `route seats` resolve from a pinned or applied commit of the policy clone, falling back to the installed table without APPLIED. Doctor reports the applied commit and distance from origin.

### Evidence

- Only gpt-implementer has a box proven by factory E2 and E9; it alone starts with a box adapter.
- The fold pipeline escalates at fix round 2. The factory scoreboard found 18 of 32 pieces ended needs_orchestrator in 24 h; a second failure goes to the escalation seat instead of a third cheap round.

### Alternatives

- Read the managed table by default: rejected because it can be half-installed and has no commit. It remains the fallback without APPLIED, labelled `source: installed` with its hash (owner steer, 2026-09-27).
- Push rules to machines: rejected; each machine pulls.
- Keep a model or seat list in each template: rejected as the drift this removes.

### Consequences

- Every machine installs four more managed seats. A differing local copy is checkpointed before replacement.
- `route seat` follows APPLIED after sync and answers from the installed table elsewhere; it fails only when neither exists.
- An adapter beyond codex, claude-code, cursor and devin needs a `BOX_ADAPTERS` edit.

## ADR-0005: Cursor and Devin accounts are leased per attempt from the seat registry

- **Date:** 2026-09-27
- **Status:** Accepted
- **Scope:** clients/seat

### Context

The owner said on 2026-09-27, "we want a single source for our credentials and that includes cursor and devin since agent lb is core to this", and later, "make sure we're not arbitrarily making barries to making this work well - we can secure things later."

### Decision

The seat registry remains the single source. A locked state update rereads the registry, merges each writer's changes and prunes leases seven days after release or expiry. Cursor API-key and Devin data-dir accounts may be leased per attempt; keychain logins cannot travel. Active leases count as use and spread account choice. A private per-attempt bundle holds credentials and only metadata is printed or stored. Release records an outcome and updates observed use, cooldown and health. An auth failure requires explicit rejection by an independent auth-only probe before it marks auth false. Logs and probe answers are never stored as diagnostics.

### Evidence

Cursor API-key accounts already use an in-memory credential store (7bad8d3f). The Devin credentials file holds a static key and server URLs, with no refresh token (field names checked 2026-09-27), so a copy elsewhere should not invalidate the source. S-11 requires auth confirmation before a failed call flips durable health.

### Alternatives

Proxying through the LB is impossible because each CLI talks to its own backend. Long-lived copies on other machines lose the single source. Neither vendor currently offers per-attempt scoped tokens; that belongs to later hardening.

### Consequences

A long-lived key leaves this machine for the length of an attempt. The bundle uses 0600 files and 0700 directories; no secret appears in outputs. By the owner's steer this change ships with one cross-vendor reviewer rather than three lenses; the hardening review belongs to the hardening piece.

### How it changes

A new entry with new evidence supersedes this one. This entry is not edited.

- 2026-09-27 21:55 UTC (F10d): Cursor and Devin forwarder definitions now carry a separate factory-box prompt; Studio forwarders ignore it and retain their existing routing metadata.
- 2026-09-28 03:10 UTC (PC-first, supersedes ADR-0004's first evidence line): opus-seat, cursor-seat and devin-seat gain box adapters (claude-code, cursor, devin). Evidence: on 2026-09-28 one real unit per seat ran on the pc-wsl box through `factory job submit` and `job wait` with rules pinned to an unpushed commit carrying exactly this change, and each came back applied with seat rc 0, check rc 0 and tokens counted: pcfirst-codex.20260928T030439-d8ff (gpt-implementer), pcfirst-claude-code.20260928T030442-4528 (opus-seat), pcfirst-cursor.20260928T030443-45fd (cursor-seat, leased cursor-gmail, released), pcfirst-devin.20260928T030445-0ad5 (devin-seat, leased devin-main, released). Studio's own Cursor and Devin logins probed auth_ok afterwards. A unit reaches a box only when its workflow sets a host, so runs without one are unchanged.
- 2026-09-28 UTC (Sonnet 5.5): the `sonnet` alias and sonnet-latest mean claude-sonnet-5-5, and claude-sonnet-5 joins `retired`. Evidence: Claude Sonnet 5.5 shipped 2026-09-28 at Sonnet 5's price; Claude Code 2.1.284 (the newest on npm) still maps `sonnet` to claude-sonnet-5 (`claude -p --model sonnet` reported claude-sonnet-5), and a direct `/v1/messages` call through the LB with model claude-sonnet-5-5 returned model claude-sonnet-5-5 (x-request-id fa366de9-fe7e-4533-8748-2643426fd40c). install-policy writes `ANTHROPIC_DEFAULT_SONNET_MODEL` so the pin reaches every machine through the sync. Owner: "we should absolutely end to end ensure we're not using the old sonnet anywhere and fully embracing this."
- 2026-09-28 UTC (default implementer): sonnet-implementer (sonnet-latest, high) leads the implement and mechanical chains, gpt-implementer (Sol medium) next; `implement_default` is the one-line revert. Sol implements only when Sonnet is really out (the fold re-seating after two infra failures on 429s or usage-limit errors, or routing state recording the seat down), never on a percentage; its code then gets the Opus verifier. Evidence: Alex, 2026-09-28 ~19:30Z, "most of our default work should be routed to sonnet in general, just validated by sol and opus; scoping still done in opus", and ~19:40Z, "only when we are really limited". Provisional: E12 (Sonnet 5.5 high vs Sol medium vs Opus medium) is the check; revert if Sonnet 5.5 accepts more than one unit fewer than Sol or costs more per accepted unit.
- 2026-09-28 UTC (dynamic Claude aliases): opus-latest, sonnet-latest and haiku-latest resolve from the upstream Anthropic model list (LB `/api/models/anthropic`, then route's day cache, then the alias's `pinned` id), and install-policy pins `ANTHROPIC_DEFAULT_SONNET_MODEL` to the resolved Sonnet, so a new Claude release needs no edit. Owner: "make 'latest' as a model tag route to the latest so we don't need to update." Review (codex-verifier, Sol xhigh): three lenses FAIL on the first diff, one fix round, then two concrete items fixed (the refresh lock, created per event loop). Opus overrides the rest with reasons: the endpoint keeps `/api/pools`' dashboard dependency, which route already reads without a session; `scripts/install-claude-clients.sh` does not bundle `clients/route` and so pins the fallback, which is out of this change's scope, and coding-agents-sync exports `clients` with the policy and re-pins every five minutes.
- 2026-09-28 UTC (implement fallback, supersedes "default implementer" above): gpt-implementer (Sol medium) is the default implementer again; sonnet-implementer (sonnet-latest, high) is the Codex-empty fallback, then opus-seat, which also takes judgment code and whatever Sonnet fails. The trigger is real Codex 429s or usage-limit errors, never a low percentage. Evidence: orch-lab E12 (E10 units, n=6 per batch, provisional), per accepted unit Sol medium 6/6 at 379k fresh tokens and $0.86, Sonnet 5.5 high 8/12 at 565k and $2.76, Opus medium 4/6 at 683k and $3.76 (agent-lb request_logs, list price); the earlier entry's own revert test was met. Factory's fold re-seats in the same order (factory e576cee).
- 2026-09-30 UTC (review depth by judgment): money-path changes still get three lenses by default, but the lead may run fewer when judgment says the risk is covered (for example a comment or doc edit inside a money-path file, or a one-line constant with its own test) and must record the count and reason in a `Lenses:` trailer. Any FAIL still blocks, and overriding one still needs the owner. Evidence: Alex on the routing iteration 2 scope, T6 (2026-09-30): "don't need super hard rules, would rather have judgement decide. dont waste tokens but maintain qualilty"; he approved the scope at 10:39 ET. E16 field (2026-09-30): 76 Sonnet-reviewed PRs merged overnight with none reverted in the window, while #3477 took 24 verdicts (8 diffs x 3 lenses).
- 2026-10-02 UTC (one worktree layout): the verify seat definitions put disposable verify trees and their `.verify-runs` output in `<repo>-wt/`, the one layout for every worktree (`/Volumes/StudioExt/repos/<repo>-wt/<topic>` on Studio; the global user rules carry it, ROUTING.md stays at its 150-line cap); `<repo>-worktrees/` and sibling trees are retired. Evidence: owner steer 2026-10-02 18:58 ET (nav-retro T4, "Keep agent-rails-wt/<topic>, retire the other two"); on 2026-10-02 agent-rails had 284 trees in `-wt/`, 231 siblings and 35 in `-worktrees/`, written by three contradicting rules (`~/.agent-rails/scoping/nav-retro/research/path-sprawl/ground.md`).
- 2026-10-05 03:10 UTC (no job counts): ROUTING.md Parallelism drops "about 15 concurrent jobs on one 16-core host" as a width rule. Owner, 2026-10-04: "blow away any job limit that we had end to end" and "the joblimits ... are stale? update this and all the according rules". The target is width from factory-admit's measured signals (factory repo, admission series; A1a-A2a landed). As of this entry every host runs `observe`, so legacy caps still set width until a host runs `signals` and its wrappers defer (verify-slot reads a fixed cap with no mode check today); seat docs that queue through verify-slot or run four test workers stay until A15 deletes those caps. The 2026-09-26 slowdown stays as evidence for the load veto, not as a count.
- 2026-10-05 19:50 UTC (Codex pace guard, Grok hold): `openai-codex` joins `policy.pace.guarded_pools`, so Sol implement, mechanical and explore rungs go soft (after every open rung) while Codex is behind pace, and Sol stays first only where it is the cross-vendor reviewer of Claude-authored work. The interim grok-medium and grok-low rungs get a closed gate. Evidence: at 19:42Z the five OpenAI accounts had 25, 14, 10, 10 and 6 percent of the week left with resets on 10-09 (one reset credit redeemed on the 14 percent account); every Grok seat run on cursor-main and cursor-gmail since 12:18Z failed with zero tokens ("You're out of usage"), while Composer on cursor-gmail succeeded. The guard releases itself when the pool is back on pace; reopen Grok at the Cursor cycle reset (2026-10-30) or on a passing probe.
- 2026-10-05 20:44 UTC (Composer hold): the interim composer rungs for implement, mechanical and explore get a closed gate, so live picks go Devin SWE, then Sol (soft while Codex is behind pace), then the Sonnet stand-in. Evidence: at 20:30Z Composer seat runs on cursor-main (Studio) and cursor-gmail (ax42) both failed with "You're out of usage"; Devin swe-2-high ran on ax42. Cursor auto ran on cursor-gmail but is not a rung: nothing shows it stays inside the plan with on-demand off. Reopen at the Cursor cycle reset (2026-10-30) or on a passing probe.
- 2026-10-06 00:10 UTC (Fable and Astra readmitted for two seats): the routing table's new `readmitted` map lets `fable-orchestrator` (`fable-latest`, the orchestrator and lead tabs Alex talks to and the factory decider) use Fable and `astra-consult` (`astra-latest`, high, read-only plan second opinions, one per plan with a brief under 2k tokens) use Astra. Both families stay in `retired`, so every other seat, alias and dispatch still refuses them, and neither joins an implementer or review ladder. Evidence: Alex, 2026-10-05 20:05 ET, "right now i'm just using fable because opus has sort of failed this project. i also want you to consult with astra throughout this without burning too many tokens." A 20-token Messages call through the Studio LB returned model claude-fable-5-1 and, for gpt-6-astra-high, model gpt-6-astra (both HTTP 200, 2026-10-06 00:00 UTC). Revisit when Alex moves the tabs back to Opus or Astra stops being served.
