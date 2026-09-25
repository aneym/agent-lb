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
  mechanical ones.
  Standing opt-in (Alex via the fixer, 2026-09-25): "if a workflow would make us move
  faster, for any of the tasks, i approve use of workflows." Applies to all OF work
  (task-set building, Harbor arm runs, replay sweeps); stages default to sonnet/haiku,
  Opus only where a stage needs judgment. Keep your own context lean: state goes in this file and
  `~/.agent-lb/of/`, not in the chat.
- Restart freeze (fixer, 2026-09-25): nobody in OF kickstarts or restarts the live
  agent-lb until the fixer's lb-restart (lock, drain, health gate, rollback) lands and the
  fixer sends the command. Deploys can be staged.
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
    one receipt row. Borrowed from keel (`jev_routing.rs`): at most 16 candidates with
    ids `[A-Za-z0-9_.-]`, `escalate` reserved as abstain; the menu is fingerprinted and
    a pick expires after 45 s; a pick below confidence 0.35 or per-candidate fit 0.8 is
    treated as abstain; the decider never grants permission.
D4. **One ledger.** Decisions go to `~/.claude/logs/dispatch.jsonl` as
    `event: "of_decision"` (candidates, pick, abstain, recheck, fallback, seat, model,
    decider latency). Outcomes go as `event: "implement_result"` through `route record`
    (extended with `--decision-id`, `--ok`, `--tokens-cache`). The per-project
    `.open-factory/ledger.jsonl` and `dispatch/log.jsonl` are removed.
D5. **Harbor runs the eval** (Alex via the fixer, 2026-09-25). harbor v0.23.0
    (`~/.local/bin/harbor`), containers on OrbStack docker (apple-container as a second
    option). The question it answers: which harness config is best for OUR work.
    **Arms are harness configs**, each a Harbor agent with its model traffic going
    through agent-lb so it spends the subscriptions:
    (A1) stock `claude-code`, Opus; (A2) stock `claude-code`, Sonnet;
    (A3) `cc` + OF routing: a custom Harbor agent (reusing the useful parts of the
    abandoned `/Volumes/StudioExt/repos/of-wt-harbor/adapters/harbor/agent.py`) whose
    Claude Code driver dispatches per call through `route menu` + Jev with host
    re-check; its routing policy has sub-arms (static table, Jev, later CLM);
    (A4) `codex` on `sol-latest`; (A5) `cursor-cli` / `grok-build` on Grok;
    (A6) others as they prove runnable: `opencode` on Kimi/GLM via agent-lb, `devin`,
    `hermes`. Arms whose traffic cannot pass through agent-lb (Cursor, Devin use their
    own backends) are labelled so in the report.
D6. **Gate before any eval spend:** a containerized agent reaches agent-lb on the host
    (`host.docker.internal:2455`) and authenticates without the macOS keychain, for
    claude-code and codex, proven by a trivial Harbor task that passes and shows up in
    agent-lb's request log on a subscription account. Findings:
    `~/.agent-lb/of/harbor-feasibility.md`.
D7. **Datasets.** (1) `of-work`, our own Harbor dataset built from real agent-rails
    and agent-lb PRs: each task = the repo at the PR's base commit baked into the
    environment, an instruction written from the PR's intent (not its diff), and
    tests from the PR plus the touched module's suite as the verifier. Classes:
    small/medium implement, bug fix with regression test, mechanical sweep, and
    planted-bug review with a checkable finding. Dev split lives in git
    (`clients/open-factory/evals/of-work/dev/`); the held-out split is written by a
    separate agent into `~/.agent-lb/of/heldout/`, only its sha256 manifest is
    committed, and the orchestrator does not read it before the final run.
    (2) One public Harbor-hub dataset (Terminal-Bench style) for comparability with
    published numbers; if its full size is too costly for the Claude pool, a fixed
    seeded subset, reported as a subset.
D8. **Statistics.** k>=3 attempts per (task, arm). Report pass rate with 95% CIs from
    a task-clustered bootstrap (resample tasks, then attempts), and arm differences
    as paired bootstrap CIs over the same tasks; Wilson intervals as a cross-check.
    Cost, tokens and wall time get the same treatment. No single-number claims.
D9. **Replay for routing policies.** Harbor trials of single-seat arms (A1, A2, A4,
    A5) on the same tasks form a (task, seat) outcome matrix. Routing policies
    (all-Opus, static table, Jev, CLM, hindsight cheapest-that-passes) are scored by
    replaying their picks against it, plus their real decision cost; A3 live runs check
    that replay predicts live. Every trial keeps Harbor's trajectory and our receipt
    (tokens, cache-read ratio, serving account/pool from agent-lb logs) under
    `~/.agent-lb/of/runs/`.
    **Budget:** Claude is behind weekly pace, so trials run at most 3 concurrent, a
    batch checks `route pools` first and pauses on a critical Claude pool, and Codex
    arms carry the bulk of exploratory volume.
D10. **`open-factory start` launches `cc`** (Opus drives) with the live menu and policy
    in the system prompt. The `recommend_driver()` Fable/Astra logic goes.
D11. **Things OF does not edit.** Bridge and warm-up code belong to the fixer
    (`claude_codex_bridge.py`, `http_bridge_forwarding.py`, primer). Dynamic
    `CCGPT_MODEL_ALIASES` in `app/modules/proxy/api.py` is requested from the fixer via
    INBOX, since it sits beside the bridge work.

D12. **Startup cost is a seat dimension.** A Workflow agent or subagent starts at about
    65-100k tokens of context before doing work; built-in Explore starts near 15k. Each
    trial and each dispatched seat records its first-turn input tokens (cached and
    uncached) and the report prices a seat as startup + work, so a cheap model with a
    heavy start does not win on paper. Where Harbor's claude-code arm can use a trimmed
    agent definition, the lean variant is its own arm.

### Open questions the eval answers (not Alex)

Alex (2026-09-25, via the fixer): "i dont know the answers to your routing questions, so
that needs to be part of the evals that OF runs." Routing policy is never asked of Alex;
each question below is an arm or hypothesis with a default that runs until the measured
result replaces it. Alex's gates stay: publishing numbers, product direction.

| # | Question | Default until measured | How the eval decides |
|---|----------|------------------------|----------------------|
| Q1 | Which model per task class (Opus, Sonnet, Haiku, Sol, Luna, Grok, Kimi, GLM)? | routing table as is | (task, seat) outcome matrix; per-class pass rate and cost CIs |
| Q2 | Reasoning effort per class (low/medium/high/xhigh)? | high for Opus driver, table effort for Codex | effort as a seat dimension on a subset (Opus, Sol) |
| Q3 | When does GPT/Grok/Cursor beat Opus at equal or better pass rate? | Opus for core implement while on pace | paired bootstrap per class, A1 vs A4/A5 |
| Q4 | Harness: stock claude-code vs cc+OF vs codex vs cursor-cli vs others? | cc + OF | Harbor arms A1-A6 (D5) |
| Q5 | Delegation shape: inline, subagent, teammate or Workflow? | subagent for volume, inline for small | A3 variants on the same tasks; includes startup cost (D12) |
| Q6 | Decider: static table vs Jev vs CLM vs hindsight bound? | Jev with host fallback | policy replay on the matrix + A3 live subset (D9) |
| Q7 | Jev thresholds (confidence 0.35, fit 0.8) and capability blurbs? | keel's values, decider.json v1 | replay sweeps on dev only; held-out confirms |
| Q8 | Fallback order when a pick fails re-check or a pool runs dry? | `route pick` chain, then driver | fallback rate and success of fallen-back runs |
| Q9 | Pace gates (Opus `min_pace` -10, low band = 2 eligible accounts)? | table values | replay with gates on/off vs pool burn per window |
| Q10 | Cross-vendor audit on implement: worth its cost? | optional (ROUTING.md 2026-09-25) | A3 with/without audit: defects caught vs tokens and time |

### Milestones (status)

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Orient, plan (this section) | done 2026-09-25 |
| 2 | One router: `route menu` (menu builder with live headroom and resets), OF on `route`, catalog + `recommend_driver` gone, receipts to dispatch.jsonl | done 2026-09-25 (`jev pick` added in the jev repo, e6f17bf) |
| 3 | Harbor gate (D6): container -> agent-lb auth for claude-code and codex, smoke trials | done 2026-09-25 |
| 4 | `of-work` v0: 10 dev tasks from real PRs as Harbor tasks + sealed held-out manifest | next |
| 5 | Eval v0: arms A1, A2, A4 on dev, k=3, CIs, receipts | |
| 6 | A3 (cc + OF routing) as a Harbor agent; Jev sub-arm with re-check/abstain/fallback; policy replay | |
| 7 | A5/A6 arms, GPT-inside-Claude-Code after the bridge fix, public dataset subset | |
| 8 | Held-out run, report (pretty-doc on tailnet), Alex review, publish on approval | |

### Log

- 2026-09-25: plan written. Keel/Jev digest at `~/.agent-lb/of/keel-jev-digest.md`.
- 2026-09-25: eval moved onto Harbor (D5-D9) at Alex's request via the fixer.
- 2026-09-25: M3 Harbor gate passed (`~/.agent-lb/of/harbor-feasibility.md`). OrbStack
  containers reach agent-lb at `host.docker.internal:2455` (loopback-forwarded, so agent-lb
  treats them as local and keyless; attribution is by sessionId via /api/request-logs).
  claude-code/claude-sonnet-5 reward 1.0, 69,967 in / 34,904 cache-read, one Max account,
  second request cache-read (LB cost $0.088 -> $0.0076); codex/gpt-6-luna reward 1.0 on one
  Pro account. Flags: `--ae ANTHROPIC_BASE_URL=... --ae ANTHROPIC_AUTH_TOKEN=...` /
  `--ae OPENAI_BASE_URL=.../v1`; full model ids. Agent install is 3-6 min of each trial, so
  CLIs get pre-baked into images and install-skipping agent subclasses before any sweep.
  The `of-harbor` key (7d6b0004) exists for non-local paths (tailnet, apple-container);
  lb-harbor-access (fixer) owns it. Old of-wt-harbor adapter is dead; A3 subclasses the
  built-in ClaudeCode agent. Public dataset: terminal-bench@2.0 (89 tasks; published
  Claude Code and Codex numbers exist), terminal-bench-sample@2.0 (10) for iteration.
  cursor-cli and devin cannot route through agent-lb and need CURSOR_API_KEY/DEVIN_API_KEY;
  grok-build can run Claude/GPT through agent-lb, native Grok needs an xAI key. These
  keys are requested from Alex only when A5/A6 are next.
- 2026-09-25: fixer landed dynamic GPT aliases (agent-lb 3f80283f): `/v1/messages` takes any
  served `gpt-*` plus `sol-latest`/`luna-latest` with optional effort suffix; unserved names
  get a 400 before upstream. Retired slugs still route when named, so `route` keeps its
  retirement check. Effort (Q2) is now a per-call model-name suffix for GPT seats.
- 2026-09-25: fixer's lb-harbor-access landed agent-lb 561e12eb: sk-clb keys are honored
  from loopback/containers (x-api-key too), so every trial sends `of-harbor`
  (`~/.agent-lb/of/of-harbor.key`). Plain base URL, no launcher: Max pool, one account per
  session, 100% cache-read after the first call, host and OrbStack; gpt-6-sol-low through
  the bridge 99% cached. Receipts: `~/.agent-lb/runtime/agent-lb/scripts/request_log_query.py
  --session <id> | --key of-harbor --json` (account, pool, model, tokens, cache, latency).
- 2026-09-25: in-session GPT seats `gpt-explorer` (gpt-6-luna) and `gpt-implementer`
  (gpt-6-sol) appeared; they join Q1 as seats.
- 2026-09-25: fixer landed the GPT bridge fix (444b5a66, ccgpt e2e PASS: Sol/Luna 13 s,
  Sonnet driver + gpt-6-sol subagent 15 s). GPT-inside-Claude-Code is unblocked: A3 gains
  in-session GPT seats (Agent/`agent()` with `model: gpt-6-*`) beside the codex-sol
  forwarder, measured as separate seats since their startup cost differs (D12).
- 2026-09-25: standing Workflow opt-in recorded. Planned uses: M4 fan-out converting dev
  candidates into Harbor task dirs (sonnet build + haiku oracle-run check per task), M5/M7
  arm batches, D9 replay sweeps.
- 2026-09-25: routing questions become eval arms (Q1-Q10); startup token cost added (D12).
- 2026-09-25: milestone 2 landed. `open-factory route` measured live: Jev 370-750 ms,
  ~$0.00005 per pick. Observation for the eval: the routing table still puts `codex-sol`
  first for explore/research although ROUTING.md (dfe1ea2d) made Claude the default
  worker; A3's static sub-arm uses the table as it is, and the eval decides.
