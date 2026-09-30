# Canonical coding-agent routing

Host-neutral path: `~/.agents/policy/coding-agents/ROUTING.md`. The evidence below is small-n: E3 used 3 units, E6 and E10 six each, E7 eight candidates. This file is the canon. A machine's `~/.claude/rules/models.md` may add local steers; it changes a rule here only with the same evidence, and the change comes back here.

## Who does what (owner, 2026-09-25; 2026-09-26)

Owner, 2026-09-25: "Opus plans and consults the latest Sol at high effort on complex things; cheaper agents implement
the raw code that comes out of those decisions."

1. **Opus plans, specs, designs and verifies.** On complex or risky work it asks `sol-consult` (newest Sol, high) for
   a second opinion before the work is split (`plan.second_opinion`). Design stays on Opus (`frontend-designer`).
2. **Every piece names its files, fixed interfaces and one check command.** A seat is trusted only as far as its
   check reaches. A piece with no check gets smaller, or gets its check first. Orch-lab E5 (5 units): an
   end-to-end-only check passed a real regression that existing tests plus an implementer-written test caught.
3. **`gpt-implementer` (Sol, medium) writes all code to a spec**, mechanical edits included, and the fresh Opus
   `verifier` reviews it. `opus-seat` takes judgment code by name. When Codex is truly empty (real 429s or
   usage-limit errors, never a low percentage), `sonnet-implementer` (newest Sonnet, high) stands in first and
   `codex-verifier` reviews it; `opus-seat` takes whatever Sonnet fails (`codex_empty_fallback`). The factory fold
   re-seats after two infra failures in that order. With Codex empty, Sol is out and no Claude entry has its Sol
   auditor (rule 1), so `route` picks nothing and the fold's order decides. Orch-lab E12 (2026-09-28, n=6
   per batch, provisional): Sol medium 6/6 at $0.86 per accepted unit; Sonnet 5.5 high 8/12 at $2.76; Opus medium
   4/6 at $3.76. `implement_default` in `routing-table.json` names the seat route puts first.
4. **Luna has no seat.** 2026-09-29 (Alex): "swap all gpt routing to sol latest, 6.1 please. make sure
   this is fully ingrained in and automatic." Every GPT seat uses the floating `sol-latest` alias.
5. **Read-only lookups go to `gpt-explorer` (Sol, low) or `Explore` (Sonnet, medium).** 2026-09-29 (Alex):
   every GPT seat runs sol-latest. No decision rides on them. First call: about 3.4k tokens for gpt-explorer,
   13k for Explore.
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

Effort is fixed per stage; no per-request router sits in the hot path. Higher effort buys verification, not a better approach, which comes from the plan. `verify-routing` checks every chain entry against `policy.stage_effort`.

| Stage | Effort |
|---|---|
| Relay a contract (forwarders) | low |
| Search and read | low to medium |
| Implement to a spec | medium on Sol, high on Sonnet |
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
- **Waits in subagents (2026-09-29, token audit).** A subagent's or workflow agent's cache lives 5 minutes; a lead's lives 1 hour.
  An agent with more work after a wait never blocks a single tool call past 270 s: run builds, tests, CI and review waits in the background (Bash run_in_background, `seat-run --bg --name <n> -- <cmd>`) and poll with waits of 270 s or less (Monitor timeout, `seat-run --wait <n> --max 270`).
  Never sleep or until-loop past 270 s in one call. Forwarder seats keep the wait contract in their own definition. Evidence: waste.ttl_expiry was 57 points in the 7d audit, about 73 points a week at the 09-26..29 rate; 82% of 1,172 sampled expiries followed one blocking call over 300 s.
- **Relaunching a Codex lane writer resumes its thread** (`codex exec resume <thread-id>`) instead of starting a new thread with the same prompt.
  A supervisor that has relaunched one lane 3 times in 6 hours stops and writes the failure to the lane's inbox instead of relaunching. (2026-09-29 token audit: 505 cold failover relaunches of identical prompts on 09-23/24, about 28 OpenAI points; one prompt was relaunched 75 times in 31.6 h.)
- Sessions compact at 400k; keep state in files and do not poll for status. The one exception is a subagent waiting
  on a job it launched, which polls as the wait rule above allows (270 s or less per wait). Tests (2026-09-24): load the test-audit skill first.

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
- **Family aliases, never versions:** `opus-latest`, `sonnet-latest` and `haiku-latest` for Claude seats,
  `sol-latest` for Codex and ccgpt seats (Luna has no seat, 2026-09-29, Alex), `grok-latest` for Cursor, `swe-latest` for Devin.
  `route resolve <alias>` returns the newest non-retired model served (Claude: LB `/api/models/anthropic`, else the
  alias's `pinned` id; 2026-09-28); `route models` shows it. install-policy pins `sonnet` to the resolved Sonnet.
- Terra is unserved and `implementer` retired (2026-09-25). `cc` and the Claude launcher start `opus[1m]`.

## The router (owner, 2026-09-20; chains 2026-09-26)

Which seat serves which class, on which model and effort, out of which pool, is `routing-table.json` and nothing else.
`route pick <class> [--author-vendor V]` returns the first admitted seat, its chain and its auditor. Chains, best
first (2026-09-26): plan planner (Opus high), then codex-sol (Sol high), with `sol-consult` (Sol high) as the second
opinion; review plan-reviewer (Opus high), then codex-sol; explore gpt-explorer (Sol low; 2026-09-29, Alex: all GPT routing to sol latest; Luna has no seat), Explore (Sonnet medium),
codex-sol (Sol medium); research codex-sol (Sol high), Explore, opus-seat (Opus high); implement gpt-implementer (Sol
medium), sonnet-implementer (Sonnet high), then opus-seat (Opus medium, `min_pace` -10); mechanical gpt-implementer,
sonnet-implementer, then cursor-seat (`grok-latest`), devin-seat (`swe-latest`); verify verifier (Opus high), then codex-verifier (Sol xhigh), cross-vendor; computer
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

Efficiency is the machinery's job: agent-lb forwards Claude Code payloads unchanged so the prompt cache holds (it broke for 43 h on 2026-09-21 and 12 h on 2026-09-23). Run `scripts/claude_cache_eval.py` after request-path deploys.

## Planned, not live (2026-09-26)

The factory's doctor, map, hourly canary and scoreboard are planned, not built. agent-lb's per-account status line is the doctor's accounts component.

## Enforcement (2026-09-26; seats 2026-09-27)

- `hooks/seat-guard.py` denies a retired model on a subagent, its definition or a forwarder's brief, and logs the
  rest; `hooks/subagent-closeout.py` closes the ledger line.
- `install-policy.py` installs every seat definition in `agents/` (off-default seats too), `routing-table.json`
  (keeping live `overrides`), the seat guard and the CLAUDE.md block. `route seat` resolves one seat at a commit.
- `verify-routing` checks an install; `verify-routing --source-only` checks this directory with no home and no LB.
  Both fail on a retired or older-than-newest id and on the factory rules `implement-head`, `off-default`,
  `audit-cross-vendor`, `review-policy`, `stage-effort`, `second-opinion`, `routing-doc`, `seat-definitions`,
  `public-text`, `seats-map`, `escalation`.
