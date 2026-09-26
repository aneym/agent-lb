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
