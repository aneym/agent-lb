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

(orchestrator writes here)
