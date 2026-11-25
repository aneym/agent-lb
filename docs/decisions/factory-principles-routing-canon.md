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
