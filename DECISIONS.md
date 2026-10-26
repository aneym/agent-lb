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

[Decision, evidence, and follow-up routing changes](ADR-0005-SEAT-LEASES.md).

## guard-trim (2026-10-08): accidental commands, not an adversarial boundary

M3: deliberately forged alex-* records are outside this posture; reject symlinked records cheaply, but do not build provenance enforcement. M7: no-chrome and desktop are not kept floors; timeout fail-open is correct. xargs-fed deletion and SQL comment obfuscation (DROP/**/TABLE) remain accepted residuals, tracked with xfail boundary cases. Only actual floor guards belong in dispatcher fail-closed lists.

## ADR-0006: Time-boxed per-model account exclusions

- **Date:** 2026-10-22
- **Status:** Accepted
- **Scope:** `model_account_exclusions` in `app/core/config/settings_fields.py`, applied by `app/core/balancer/exclusions.py`

`model_account_exclusions` maps a model to `{account_id: until_epoch}`. Until that time the account is not selected
for that model; it still serves every other model, and if the rule would leave no account the full list is used.
An exclusion always carries an expiry; there are no permanent ones. When it expires the account rejoins, and the
windowed error drain (19cd985a) moves traffic off it again if it keeps failing. To extend an exclusion, a new
expiry has to be set on evidence: its overload rate stays well above the other accounts' for the same model.

First use: Codex accounts 2661c1c1 and 409a8db4 returned every `server_is_overloaded` on gpt-6.1-sol in the 01Z hour
of 2026-10-22 (391; the other three had none), which made prod chat first tokens 9-25 s. They are excluded from
gpt-6.1-sol until 2026-10-22T14:40Z (area-ship ruling b-20261022022947).



## Claude hands enforcement (October 22, 2026)

Team-lead's seat-guard-enforce brief supersedes warn-only hands admission: Agent
hook errors route recognizable Claude dispatches without OPUS-REASON to the
normal Codex target, or gpt-implementer when classification fails. Workflow
hands calls rewrite when statically checkable; dynamic options block. The
shared OPUS-REASON exemption and SEAT_GUARD_ALLOW_ANTHROPIC override remain.
Mac-only opus-seat briefs without OPUS-REASON forward to gpt-implementer-local
through seat-run on Studio instead of doing the work in-process.
## Hands dispatch stays off Claude (2026-10-22)

Team-lead restated Alex's decision: Opus supplies architecture and Codex executes.
Implementation, fix, fix-forward and migration resolve to implement; sweep, probe
and test-run to mechanical; investigation and exploration to explore. Dispatch
records both names. Fix retains its never-explore and Grok exclusion rules.
Implement, mechanical, explore and research default to Sol. On exhaustion they
try eligible non-Claude table arms, then wait; this supersedes the cross-vendor
Claude fallback for those classes. Opus authority classes retain their policy.

## Browser guard classifies execution, not brief words (October 23, 2026)

Alex's 02:42Z factory request authorizes fixing false browser-floor refusals.
The owner-browser and profile floor is unchanged. Browser names, `open`, and
ordinary Library log/plist paths in briefs and message arguments are data,
not browser launches. Shell wrappers, substitutions, executable heredocs,
profile reads/copies, AppleScript browser control and CDP attaches still
refuse; unreadable input or unparseable commands fail closed.

agent-lb now owns the hook and its existing Python shell/argv classifier via
install-policy, replacing the old factory-runtime symlink atomically. The
classifier, rather than a whole-command keyword regex, decides both the hook
and dispatcher prefilter. Explicit headless Playwright launches without
profile overrides use a fresh context; persistent contexts need a literal
agent-owned profile, never the owner's Library or copied profile state.
page-shot, acct-browser and Rails test-account sessions remain sanctioned.
Real hook-input subprocess cases cover refusals and legitimate proof/brief
commands; the existing Open Factory browser-floor replay is kept unchanged.

## 2026-10-23: Requested seats are not hook policy

Pick the seat by the routing rules; hooks never rewrite seats. Removed Agent and Workflow seat guards, their hands classifier and Bash steering nudge, and the OPUS-REASON exception. The policy installer retires their installed files and hook registrations. Mac-only Opus forwarding keeps Opus rather than selecting Sol. Kept-floor guards are unchanged.

## 2026-10-24: Relay wrappers run on Sol

Forwarding wrapper seats (host-relay, codex-*, gpt-*, verifier and the rest of RELAY_SEATS) render `model: gpt-6.1-sol` from the relay standard's `wrapper_model: sol-latest`. They carry no decision, and on Sonnet they were the remaining helper rows in the E1 quota matrix (lane goal-quota-routing, receipt S7b-E1). `sol-latest` pins `gpt-6.1-sol` for the workflow relay seat, matching open-factory's alias pin.

## 2026-10-26: The routing table source is a directory

The routing table source is `config/coding-agents/routing-table/`, one JSON file per responsibility (classes, explore, ladders, models, policy, pools, seats, standards). Each file holds a disjoint set of top-level keys. A reader that needs a few keys reads the file that defines them. Only `route` and `install-policy.py` compose the whole table. They read the files in name order and refuse a repeated key. The installed `~/.agent-lb/managed/coding-agents/routing-table.json` stays one generated file because agent-rails hooks, box sync and `table_sha256` depend on it, and its digest equals the source directory digest. `route` reads either form at a pinned rules commit. The policy mirror drops its old single-file copy.

## 2026-10-26: Relay launchers set TMPPREFIX

Box seats mount /tmp read-only and give a writable TMPDIR, but the seat Bash shell is zsh, which writes heredoc temp files under TMPPREFIX (default /tmp/zsh). Every relay launcher passes its contract by heredoc, so a forward from inside a box seat failed with "can't create temp file for here document" before the launcher ran. Each launcher block now sets `TMPPREFIX="${TMPDIR:-/tmp}/zsh"` after its `cd`. The `[claude-code:unrecognized_model]` line for `gpt-6.1-sol` is Claude Code's non-fatal stderr notice for a model outside its own catalog; the request still reaches the bridge and succeeds.
