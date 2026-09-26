# Canonical coding-agent routing

Host-neutral path: `~/.agents/policy/coding-agents/ROUTING.md`. The evidence below is small-n: E3 used 3 units, E6 and
E10 six each, E7 eight candidates. This file is the canon. A machine's `~/.claude/rules/models.md` may add local
steers; it changes a rule here only with the same evidence, and the change comes back here.

## Who does what (owner, 2026-09-25; 2026-09-26)

Owner, 2026-09-25: "Opus plans and consults the latest Sol at high effort on complex things; cheaper agents implement
the raw code that comes out of those decisions."

1. **Opus plans, specs, designs and verifies.** On complex or risky work it asks `sol-consult` (newest Sol, high) for
   a second opinion before the work is split (`plan.second_opinion`). Design stays on Opus (`frontend-designer`).
2. **Every piece names its files, fixed interfaces and one check command.** A seat is trusted only as far as its
   check reaches. A piece with no check gets smaller, or gets its check first. Orch-lab E5 (5 units): an
   end-to-end-only check passed a real regression that existing tests plus an implementer-written test caught.
3. **`gpt-implementer` (Sol, medium) writes all code to a spec**, mechanical edits included. `opus-seat` takes
   judgment code and anything Sol fails twice; the driver sends those to it by name. Owner, 2026-09-26: "sol and opus
   are the right models, since the others just make mistakes? we shouldnt risk mistakes if they're just going to
   make more work later."
   Router fallback, measured 2026-09-26: `route pick implement` falls back to opus-seat only when it skips
   gpt-implementer (seat recorded down, model unserved) and the Anthropic pool is on pace and above low and the
   Codex pool is above critical, because Opus's work needs a Sol auditor.
   A low Codex pool does not move implementation to Opus. An exhausted Codex pool leaves nothing routable.
4. **`luna-implementer` and `sonnet-implementer` are off the default path** (`off_default`). They return only
   through an eval that matches Sol's accept rate on that kind of task. Orch-lab E6 (2026-09-26, the same 6 units,
   specs and review template per seat, small n): Sol 6/6 at about 105k tokens per accepted unit; Luna 5/6 at about
   135k; Sonnet 5/6 at about 284k; Opus 4/6 at about 618k. A miss costs a fix round plus a re-verify.
5. **Read-only lookups go to `gpt-explorer` (Luna, low) or `Explore` (Sonnet, medium).** No decision rides on
   them. First call: about 3.4k tokens for gpt-explorer, 13k for Explore.
6. Codex CLI, Cursor and Devin are optional capacity; Cursor takes mechanical sweeps, Devin sits behind it.

## Review (2026-09-26, orch-lab E7)

This replaces the 2026-09-25 rule "no required cross-vendor audit". E7 used 8 seeded candidates with 5 planted bugs.

- **Every unit, and every fix round, gets one fresh verifier from the vendor that did not write it:** `verifier`
  (Opus, high) for GPT, Cursor, Devin, GLM and Kimi authors; `codex-verifier` (Sol, xhigh) for Claude authors. The
  brief names the author vendor. Add a release-facts check; no standing reviewer. Fresh Sol xhigh caught 4/5, fresh
  Opus high 4/5, one Opus context reused across all 8 only 3/5. Both missed a package change with no version bump.
- **Money-path units get three lenses at xhigh; any one FAIL blocks** (`policy.review.money_path`). Any-fail caught
  5/5, majority vote 3/5. Each repo names its money path and its lenses in its own AGENTS.md.
- **A verdict is bound to the diff it reviewed.** Never ship or queue work whose latest verdict is FAIL or REJECT.
  Re-verify the final diff, or log an override with the reason. On 2026-09-25 a change was queued after its
  verifier failed it, and production crash-looped from 22:59Z to 23:07Z. Two more were queued the same way.
- **At most two fix rounds** (`max_fix_rounds`; the fold-pr review gate, 2026-09-26). Then the lane fixes, splits
  or parks the unit and logs why.
- **An incident fix ships a fixture test or a check in the same change.** On the 2026-09-26 night watch a person,
  not a check, found every factory incident.
- Prove UI changes with a screenshot of the running page.

## Effort per stage (2026-09-25; 2026-09-26)

Effort is fixed per stage; no per-request router sits in the hot path. Higher effort buys verification, not a better
approach, which comes from the plan. `verify-routing` checks every chain entry against `policy.stage_effort`.

| Stage | Effort |
|---|---|
| Relay a contract (forwarders) | low |
| Search and read | low to medium |
| Implement to a spec | medium |
| Plan, spec, second opinion | high |
| Verify | high; `codex-verifier` xhigh |
| Money path and security | xhigh |
| Max | only after asking |

The driver session runs at high. Orch-lab E10 (2026-09-26, the E6 units, small n): Sol medium 6/6 at 92k tokens per
accepted unit in 14.0 min; Sol low 5/6 at 124k in 19.1 min, since its misses cost extra rounds and review is about
two thirds of all tokens. A public DeepSWE run (2026-09-26, 20 tasks, on a model now retired): low 16/20 at
$1.46 and 4.6 min per task; xhigh 17/20 at $4.42 and 12.8 min; a per-request router 16/20 at $1.50 and 19.9 min.
Artificial Analysis TBS 0.1 (2026-09-24, open-ended research): Opus 5.5 low 24%, xhigh 62%. Open-ended work with no
tight spec stays on Opus at xhigh.

## Workflows and seats (2026-09-25; 2026-09-26)

- **Lanes run as workflows whose templates carry the review rules**, not as teammates. Batch small units into one
  run. E3: headless workflow 5.7 min, planner teammate 14.2 min. E9: 17 units in 22.3 min, 15 accepted.
- **Every workflow `agent()` call passes `agentType`** and its stage effort. Seat definitions stay lean. Never use
  `general-purpose` as an implementer. First call (2026-09-25): the default workflow agent 91k to 99k tokens,
  `general-purpose` (tools "*") 48k, lean definitions 3.4k to 6.4k.
- **Claude seats never drive a browser one step at a time.** Capture each page in one windowless call and read only
  the final image. Three Sonnet seats that drove step by step used about 40M tokens each.
- Sessions compact at 400k; keep state in files and do not poll. Tests (2026-09-24): load the test-audit skill first.

## Parallelism (owner, 2026-09-25; 2026-09-26)

Owner, 2026-09-25: "hundreds of agents in parallel must always be possible". Fan out as wide as the work splits.
Back off only on 429 or usage-limit errors, an account near the end of its 5-hour window, or rising error rates.
Host CPU sets the width: job steps slowed from 5 s to 16 s past about 15 concurrent jobs on one 16-core host
(2026-09-26). `route pools` is advisory and its aggregate hides spent accounts. If agent-lb throttles, that is a bug.

## Rules change with evidence (owner, 2026-09-26)

Owner: "i'd also like our rules to be fluid over time so that as things change and models improve we can change
things." Every rule carries a date and its evidence. Revisit a rule when a new model ships (run the E6 shape on it
first), a seat's accept rate or tokens per accepted unit moves, an incident traces back to it, or the owner steers.
Dropped seats return through an eval. Record each change as a dated `DECISIONS.md` entry and edit this file and the
table together. Log which rung passed which task (`route record`, 2026-09-25) so evals can move the default down.

## Models (owner, 2026-09-22)

- **Retired**, never resolved, denied by the seat guard, failed by verify-routing: Fable (`claude-fable-*`, `fable`,
  the `claude-planner` alias), the Codex Astra family (`gpt-*-astra`), and the gpt-5.6 generation and older. The
  table's `retired` list is the authority.
- **Family aliases, never versions:** `opus-latest` and `sonnet-latest` (Claude Code's `opus` and `sonnet`),
  `sol-latest` and `luna-latest` for Codex and ccgpt seats, `grok-latest` for Cursor, `swe-latest` for Devin.
  `route resolve <alias>` returns the newest non-retired model actually served; `route models` shows the current
  resolution. Exact ids stay in tests, ledgers and pricing.
- Terra is unserved and `implementer` retired (2026-09-25). `cc` and the Claude launcher start `opus[1m]`.

## The router (owner, 2026-09-20; chains 2026-09-26)

Which seat serves which class, on which model and effort, out of which pool, is `routing-table.json` and nothing else.
`route pick <class> [--author-vendor V]` returns the first admitted seat, its chain and its auditor. Chains, best
first (2026-09-26): plan planner (Opus high), then codex-sol (Sol high), with `sol-consult` (Sol high) as the second
opinion; review plan-reviewer (Opus high), then codex-sol; explore gpt-explorer (Luna low), Explore (Sonnet medium),
codex-sol (Sol medium); research codex-sol (Sol high), Explore, opus-seat (Opus high); implement gpt-implementer (Sol
medium), then opus-seat (Opus medium, `min_pace` -10); mechanical gpt-implementer, then cursor-seat (`grok-latest`),
devin-seat (`swe-latest`); verify verifier (Opus high), then codex-verifier (Sol xhigh), cross-vendor; computer
computer-use (Sol medium), then opus-seat; council codex-sol (Sol high), then opus-seat. Implement and mechanical
units are audited by the other vendor: Anthropic authors get `codex-verifier` (sol-latest, xhigh); openai, cursor,
devin, glm and kimi authors get `verifier` (opus-latest, high).

Pace gates only seats with a `min_pace`: `pace = remaining% - (hours_to_reset / hours_in_cycle x 100)`. Such a seat is
skipped when pace is unknown or below `min_pace`, or its pool's `eligibleAccounts` count is present and 2 or less;
others have no pace gate. Any seat is skipped when its model does not resolve, its seat is down, its pool is
`exhausted`, or it shares the author's vendor on a cross-vendor class; where a class has auditors, also when its
auditor is undeclared, unresolved, down, or in an `exhausted` pool or one with 1 eligible account or fewer.
Bands are re-read every 5 minutes and before every wave. Every dispatch and closeout goes to the dispatch ledger
(`~/.claude/logs/dispatch.jsonl`). The Codex forwarders run Codex CLI; cursor-seat and devin-seat go through
`seat run`, which fails over between registered accounts and writes the receipt.

Efficiency is the machinery's job: agent-lb forwards Claude Code payloads unchanged so the prompt cache holds (it
broke for 43 h on 2026-09-21 and 12 h on 2026-09-23). Run `scripts/claude_cache_eval.py` after request-path deploys.

## Planned, not live (2026-09-26)

The factory's doctor, map, hourly canary and scoreboard are planned, not built. agent-lb's per-account status line is
the doctor's accounts component.

## Enforcement (2026-09-26)

- `hooks/seat-guard.py` denies a retired model on a subagent, its definition or a forwarder's brief, and logs the
  rest; `hooks/subagent-closeout.py` closes the ledger line.
- `install-policy.py` installs the seat definitions (now with the managed ccgpt seats gpt-implementer, gpt-explorer
  and sol-consult), `routing-table.json` (keeping live `overrides`), the seat guard and the CLAUDE.md block.
- `verify-routing` checks an install; `verify-routing --source-only` checks this directory with no home and no LB.
  Both fail on a retired or older-than-newest id and on the factory rules `implement-head`, `off-default`,
  `audit-cross-vendor`, `review-policy`, `stage-effort`, `second-opinion`, `routing-doc`, `seat-definitions`,
  `public-text`.
