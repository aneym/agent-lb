# Open Factory program

This is the brief for the Open Factory (OF) orchestrator. It is the long-term
vision, what exists today, and how we will know it works. The orchestrator owns
this program and updates this file as the plan changes. The short-term fixer
(another Claude Code session, herdr tab "usage audit") repairs the plumbing
underneath and reports into `~/.agent-lb/of/INBOX.md`.

## What Alex wants

- "make open factory a real thing that works", "actually see it through".
- "publish eval numbers and see if we can get automatic routing, across
  different subscriptions, actually working".
- "use ultracode for all claude work long term, and have a proper router balance
  based on all my tools and account connections". "always use the latest models."
- GPT, Grok and Cursor models reachable from inside Claude Code, not only as
  separate CLIs.
- "no hard rules". Claude Code is the default worker (Opus drives, Sonnet and
  Opus subagents do the work); Codex, Cursor, Devin are extra capacity.

## The target system

One Claude Code session (`cc`) is the host. For each piece of work it:

1. **Builds a menu.** The host lists the seats it can run right now: model x
   vendor x subscription account, filtered by what is installed, served, logged
   in, and has headroom in its 5-hour and weekly windows. A seat the host cannot
   run never reaches the menu.
2. **Picks.** A decision layer chooses one menu id or abstains. Jev is the
   default decider (`rails/jev.py` in agent-rails, `jev route` CLI); a static
   table is the fallback; a contrastive ranker (CLM) is a later arm. The decider
   cannot invent a seat and cannot grant permission.
3. **Re-checks.** The host confirms the pick is still valid (pool not exhausted,
   model still served) and falls back by a written policy if not.
4. **Dispatches.** Per call, not per process: Workflow `agent({model})` and Agent
   subagents with a model name, which agent-lb routes by name (Claude models to
   the Claude Max pool with sticky sessions, `gpt-*` through the
   `/v1/messages` to Codex bridge, later Grok and Gemini through a translation
   layer). Seats that only exist as CLIs (cursor-agent, devin) run through
   their forwarder agents.
5. **Records.** One receipt per decision: candidates, pick or abstain, host check
   result, fallback, seat, tokens and pool usage, wall time, and the verified
   outcome. Receipts go to the single live ledger `~/.claude/logs/dispatch.jsonl`
   (schema that `route record` expects), never to a second per-project file.
6. **Improves by replay, not by magic.** A receipt becomes a scenario. A policy
   change (menu rules, decider prompt, fallback, table) is replayed against the
   same scenarios, compared with the baseline, and a human approves the new
   baseline. No silent self-training.

This is the "host owns the move" pattern from the keel article
(https://x.com/Av1dlive/status/2102802621664985241, repo
github.com/codejunkie99/keel, see `crates/engine/src/jev_routing.rs` and
`docs/decision-architecture.md`). Also read
https://academy.dair.ai/resources/jev-decisions-in-a-pi-sdk-harness (omarsar0 on
Jev vs CLM as System One deciders).

## What exists today (verified 2026-09-25)

- `clients/open-factory/` (this dir): ~700-line stdlib CLI, commands
  `seats doctor init status route start report`, a 13-seat `catalog.json`, and
  `start` which writes the seat allowlist and rules into the driver prompt.
  Problems: `catalog.json` lists retired models (gpt-5.6-*, astra, fable);
  `recommend_driver()` (cli.py ~96-127) picks the driver from one
  `/api/accounts` snapshot; the per-project `.open-factory/ledger.jsonl` is
  written by init only and read by nothing; choice happens per launched process,
  never per call. Keep: putting live policy into the orchestrator prompt.
- `clients/route` (~1600 lines) with
  `~/.agent-lb/managed/coding-agents/routing-table.json` v2: class fallback
  chains, `resolve <alias>` to the newest served model, pool pacing,
  `pick <class>`, `record`, `learn`, `doctor`, `pools`. This is the real router;
  OF should call it instead of keeping its own catalog.
- `~/.claude/logs/dispatch.jsonl` (~5.7k rows): the live ledger.
- agent-lb routes `/v1/messages` by model name. `CCGPT_MODEL_ALIASES`
  (`app/modules/proxy/api.py` ~769-784, 876-886) is a hardcoded list
  (gpt-6-sol, gpt-6-luna and -low/-medium/-high/-xhigh); it should resolve
  dynamically like `route resolve`.
- Claude caching through agent-lb is healthy since 3eb7c184 (95-98% cache
  reads). Sessions stay on one account until a real 429 (a25a6f5b). Compaction
  for `cc` is `--autocompact 300k` in `~/.zshrc` `_cc_lb`.
- CLIProxyAPI (github.com/router-for-me/CLIProxyAPI, Go, MIT) is the reference
  for provider breadth (Claude, Codex, Gemini, Grok/xAI, Kimi) and for correct
  streaming translation; learn from it, maybe run it later as a sidecar for
  Grok/Gemini only.

## Blockers the fixer owns (do not edit these files; build around them)

- **GPT agents hang.** Workflow and subagents on `gpt-6-*` get no first byte:
  the bridge buffers until the first content block and never emits reasoning, so
  Claude Code waits 181 s and retries up to 10 times. Fix in progress in
  `app/modules/proxy/claude_codex_bridge.py` / `http_bridge_forwarding.py`, with
  an e2e eval `scripts/ccgpt_e2e_eval.py`. Until it lands, GPT arms are blocked.
- **No per-agent timeout in Workflow scripts** (no timers, no clock). The
  guarantee has to come from agent-lb failing fast. A feature request was filed.
- **Account warm-up.** agent-lb should start each account's 5-hour and weekly
  windows early so they reset early (primer commits 63299c12 / 400bf021, branch
  `fix/anthropic-primer-continuous`); status unverified.

Ask the fixer for anything in agent-lb's request path by appending to
`~/.agent-lb/of/INBOX.md` (one dated entry, what you need and why).

## How we know it works: the eval

Publish numbers only from runs you observed. The design:

- **Task set.** Fixed and versioned, split into a dev set and a held-out set you
  never tune on. Draw tasks from real work: dispatch.jsonl history and merged
  PRs in agent-rails and agent-lb, across classes (explore, research, small
  implement, medium implement, review/verify, mechanical). Each task needs an
  executable check (tests, a diff predicate, a verifier with a rubric) so success
  is not a model's opinion.
- **Arms.** (a) everything on Opus; (b) static routing table; (c) Jev over the
  host menu; (d) later CLM; (e) cheapest-that-passes as a hindsight bound.
- **Metrics per task.** Verified success, wall time including the decision call,
  tokens and share of each subscription window consumed, retries, abstain and
  fallback rate, cache read ratio, human review needed.
- **Across subscriptions.** Report how load spread over the Claude Max accounts,
  ChatGPT/Codex accounts, Cursor and Devin, and whether any pool hit its limit.
- **Receipts.** Every run writes replayable receipts under `~/.agent-lb/of/runs/`
  (outside git). The report cites them.
- **Publishing.** Build the report as a pretty-doc on the tailnet
  (https://studio.tailf266ac.ts.net:8799/...). Posting it publicly is an
  outbound action: draft it and ask Alex first.

## First milestones

1. Orient: read this file, `clients/route`, `routing-table.json`, cli.py, the
   keel decision docs. Write the plan and decisions below this section.
2. One router: OF calls `route pick` / `route resolve`; delete `catalog.json` and
   `recommend_driver()`; one ledger (dispatch.jsonl).
3. Menu builder: a function that returns the eligible seats with live pool
   headroom and window reset times, used by both `route` and the decider.
4. Eval harness v0 on Claude-only arms (works today): 10 dev tasks, arms (a) and
   (b), real numbers, receipts.
5. Jev arm (c) with host re-check, abstain and fallback recorded.
6. GPT and Cursor/Grok arms once the fixer's bridge fix lands.
7. Held-out run, report, Alex review, then publish.

## Working rules

- Work in `/Volumes/StudioExt/repos/agent-lb-worktrees/open-factory` on branch
  `of/program`. Follow agent-lb `AGENTS.md`. Merging to agent-lb `main` is
  allowed once your own tests and evals pass on the head you merge.
- After any change under `app/core/anthropic` or `app/modules/proxy/anthropic*`,
  run `scripts/claude_cache_eval.py`. Never add, remove or reorder system blocks
  on a Claude Code payload.
- Load `~/.agents/skills/test-audit/SKILL.md` before writing tests; prefer
  end-to-end evals with receipts.
- Use Workflows for fan-outs; Opus designs, `sonnet` runs volume stages, `haiku`
  mechanical ones. Keep your own context lean: state goes in this file and
  `~/.agent-lb/of/`, not in the chat.
- Never print secrets. No macOS keychain prompts (Alex is remote). No messages
  to real people. Do not touch the agent-rails repo except to read.
- Report to Alex only milestones and hard blockers.

## Plan and decisions

State files outside git: `~/.agent-lb/of/` (`INBOX.md` to the fixer, `runs/` receipts,
`heldout/` sealed tasks, `STATE.md` orchestrator handoff).

### Live picture at kickoff (2026-09-25 18:40Z)

- Claude Max: 6 accounts, 3 weekly-exhausted (reset 09-27 to 09-30), 1 at 7% weekly,
  2 healthy. `anthropic-general` weekly pace -29.6 (behind). Claude is the scarce pool.
- Codex: 4 Pro accounts, 3 active (44-73% weekly), 1 `reauth_required`. Kimi and GLM:
  1 account each, unmetered. Cursor: `grok-4.7-*` served.
- `dispatch.jsonl` rows are thin: 823 of 908 dispatch rows have no model, no row has a
  class. It cannot score routing yet; OF receipts must carry the decision.

### Decisions

D1. **`route` is the only router.** OF keeps no catalog. New `route menu [--class C]
    [--json]` returns every seat the host can run now (seat, resolved model, vendor, pool,
    pool status, eligible accounts, 5h and weekly headroom, weekly pace, next 5h and
    weekly reset) plus the excluded seats with a reason. `route pick` becomes "first
    menu entry in the class chain", so both share one eligibility function. OF talks to
    `route` over its JSON CLI, not by importing it.
D2. **Menu unit is seat x model x pool, not account.** agent-lb owns account choice
    (sticky sessions, failover). Account spread is measured after the fact from LB
    request logs keyed by session id, not chosen by the host.
D3. **Decider contract.** Input: task text, class hint, menu ids with facts. Output: one
    menu id, or abstain. The host re-checks the pick against a fresh `route menu`
    (pool not exhausted, model still served); on abstain or failed re-check it takes
    `route pick <class>`; if that is unroutable, it takes the driver (Opus). Every step is
    one receipt row.
D4. **One ledger.** Decisions go to `~/.claude/logs/dispatch.jsonl` as
    `event: "of_decision"` (candidates, pick, abstain, recheck, fallback, seat, model,
    decider latency). Outcomes go as `event: "implement_result"` through `route record`
    (extended with `--decision-id`, `--ok`, `--tokens-cache`). The per-project
    `.open-factory/ledger.jsonl` and `dispatch/log.jsonl` are removed.
D5. **Eval = outcome matrix + policy replay.** Run each (task, seat) cell for real, k
    times, and record receipts. Score each arm (all-Opus, static table, Jev, CLM,
    hindsight cheapest-that-passes) by replaying its picks against the matrix, adding
    the arm's real decision cost. This makes every new policy cheap to evaluate and is
    the "improve by replay" loop. A smaller live end-to-end run (decider -> recheck ->
    dispatch inside `cc`) proves the loop works and checks that replay predicts live.
D6. **Task set.** Each task = repo + base commit + prompt + executable check + class.
    Sources: agent-lb and agent-rails commits that ship a regression test (check = that
    test passes and the touched module's suite stays green), planted-bug reviews (check
    = finding names the file and defect), codebase questions with fixed answers
    (check = required facts present, verified by a rubric verifier), and mechanical
    sweeps (diff predicate + tests). Dev set (10, then 30) lives in git under
    `clients/open-factory/evals/tasks/dev/`. The held-out set is written by a separate
    agent into `~/.agent-lb/of/heldout/`; only its sha256 manifest is committed, and
    the orchestrator never reads it before the final run.
D7. **Executors per seat, headless.** Claude seats: `claude-lb-launch -p --model M
    --output-format json` with `CLAUDE_LB_MINIMAL=1`, in a fresh git worktree per run
    under `~/.agent-lb/of/runs/<run>/`. Codex seats: `codex exec` (does not need the
    bridge). Cursor seats: `cursor-agent -p --model grok-*`. GPT *inside Claude Code*
    (subagent `model: gpt-6-*`) waits for the fixer's bridge fix; it is its own arm.
D8. **Budget.** Claude is behind pace, so v0 runs 10 tasks x {opus, sonnet} x k=1,
    at most 3 concurrent runs, and checks `route pools` before each batch; a
    critical Claude pool pauses the batch. Codex/Cursor cells are cheap to add and
    carry most of the new volume.
D9. **Metrics per cell.** Verified success, wall time, tokens in/out/cache-read, cost
    estimate, retries, account and pool that served it (LB logs), cache-read ratio,
    whether a human would need to review (check was rubric, not tests).
D10. **`open-factory start` launches `cc`** (Opus drives) with the live menu and policy
    in the system prompt. The `recommend_driver()` Fable/Astra logic goes.
D11. **Things OF does not edit.** Bridge and warm-up code belong to the fixer
    (`claude_codex_bridge.py`, `http_bridge_forwarding.py`, primer). Dynamic
    `CCGPT_MODEL_ALIASES` in `app/modules/proxy/api.py` is requested from the fixer via
    INBOX, since it sits beside the bridge work.

### Milestones (status)

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Orient, plan (this section) | done 2026-09-25 |
| 2 | One router: `route menu`, OF on `route`, catalog + `recommend_driver` gone, receipts to dispatch.jsonl | in progress |
| 3 | Menu builder with live headroom and resets (lands with 2 as `route menu`) | in progress |
| 4 | Eval harness v0: 10 dev tasks, opus + sonnet cells, arms (a) all-Opus and (b) static, receipts | next |
| 5 | Jev arm (c): replay + a live end-to-end subset with recheck, abstain, fallback | |
| 6 | Codex (`codex exec`), Cursor/Grok cells; GPT-in-Claude-Code arm after bridge fix | |
| 7 | Held-out run, report (pretty-doc on tailnet), Alex review, publish on approval | |

### Log

- 2026-09-25: plan written. Keel/Jev decision docs digest pending (subagent).
