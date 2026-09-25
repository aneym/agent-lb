# Canonical coding-agent routing

Host-neutral path: `~/.agents/policy/coding-agents/ROUTING.md`. The history
this file used to carry (seat lineups from 2026-07 through 2026-09) is in git.

**Which model or seat does which job is `~/.claude/rules/models.md`.** It is
local to each machine, kept current, and wins over this file and the routing
table wherever they disagree. In short (2026-09-25): Opus plans, specs, designs
and verifies, with `sol-consult` (newest Sol, high) as the second opinion on
complex work; `frontend-designer` and every design decision stay on Opus.
Implementation climbs a ladder, cheapest first, one rung after two failed tries
on a piece's check: `luna-implementer` (newest Luna), `gpt-implementer`
(newest Sol), `sonnet-implementer`, then Opus. The GPT seats are Claude Code
subagents through the agent-lb ccgpt bridge, not Codex CLI forwarders; their
definitions are local and unmanaged, so the installer leaves them alone.

## Claude-vertical default (owner, 2026-09-25)

Alex, 2026-09-25: "we need to make our prompt rules looser - dont assume we're
routing work to codex. i'd rather optimize agent lb to fully allow us to use
claude vertically and occasionally use codex, but i dont want hard rules."

This section overrides anything below that says otherwise.

- **Claude Code does the work**: the driver, its subagents and its teammates.
  Implementation runs on Claude Code subagents along the models.md ladder
  (the GPT rungs go through the ccgpt bridge); Opus implementing is the last
  rung, not a violation.
- **Codex CLI, Cursor and Devin are optional capacity.** Use them when the Claude pools
  are tight, for large parallel or mechanical sweeps, or for a second opinion.
  `route pick` and the class rankings below are suggestions, not gates.
- **No required cross-vendor audit.** Verify work by running it (end to end,
  real output). A cross-vendor review is a tool for risky changes.
- **Efficiency is the machinery's job**: agent-lb keeps sessions on one
  account until it is really exhausted and forwards Claude Code payloads
  unchanged so the prompt cache holds (`scripts/claude_cache_eval.py`,
  `cache-watch`). Sessions compact at 400k and keep state in files.

## The lineup (owner, 2026-09-22)

Alex, 2026-09-22: "remove all seats that use fable, opus is good now"; "opus
5.5 and sol 6 for orchestration and planning, cheaper agents that we audit
with better models for implementation"; "NEVER use gpt 5.6 sol anymore ... we
should default to opus 5.5 for orchestration"; "ensure it just uses the latest
model in that class by default if possible"; "NO CODEX ASTRA. codex sol".

- **Retired, never resolved, denied by the seat guard, failed by
  verify-routing:** Fable (`claude-fable-*`, `fable`, the `claude-planner`
  alias), the whole Codex Astra family (`gpt-*-astra`), and the gpt-5.6
  generation and older. The table's `retired` list is the authority.
- **Models are named by family alias, never by version.** Claude seats use
  Claude Code's `opus` and `sonnet` aliases (`opus-latest`, `sonnet-latest` in
  the table), which the installed Claude Code maps to the newest model of that
  family. Codex and ccgpt seats use `sol-latest` and `luna-latest`; Cursor seats use
  `grok-latest` (medium-fast) and `grok-latest-low`; Devin seats use
  `swe-latest` (SWE-2 high, free on the subscription). `route resolve <alias>`
  returns the newest non-retired model actually served (the LB's model list
  for Codex, `cursor-agent --list-models` for Cursor, `devin models list` for
  Devin), so a new release needs
  no edit; `route models` shows the current resolution. Exact ids stay only in
  tests, ledgers and pricing.
- **Classes.** The routing table maps `implement` to gpt-implementer
  (`sol-latest`, medium), then sonnet-implementer, then Opus behind the pace
  gate; `mechanical` to luna-implementer (`luna-latest`, medium), then
  `grok-latest` on Cursor, then `swe-latest` on Devin. Plan and review:
  `opus-latest`. Explore and research: `sol-latest`, `sonnet-latest`,
  `opus-latest`. Computer use: `sol-latest` through Codex computer use, else
  `opus-latest`. `route pick <class>` reads the table.
- **Top-level sessions.** `cc` and the Claude launcher start the newest Opus
  (`opus[1m]`); herdr-tab agents run Opus.
- **Terra is unserved** (2026-09-25): gpt-6-terra is not in the upstream
  catalog and gpt-5.6-terra is retired, so no seat uses `terra-latest`. The
  `implementer` seat that forwarded to it is retired.

## Operating rules (owner, 2026-09-22)

- **One owner per app.** If a herdr tab owns an app, no teammate runs a
  parallel lane on it.
- **Tabs coordinate directly with each other.**
- **Never ask Alex** for merges, deploys, releases that have a rehearsal and a
  backup, or infra fixes, including accounts and keys he already scoped. Do
  them and report the result. Product direction and visual sign-off go to
  Alex.
- **Every UI change is screenshot-verified.**
- **Fan out** as wide as the work splits; back off only on real limits
  (models.md, Parallelism).

## The rule (owner, 2026-09-20: adaptive; lineup amended 2026-09-22)

Every pool is a weekly budget with a known reset, and what is unspent at the
reset is lost. So the router does two things at once: spend every pool on pace
to its reset, and put the strongest seat that pace allows on each class.

1. **Pace, not headroom, decides.** For each pool: `pace = remaining% -
   (hours_to_reset / hours_in_cycle x 100)`. Ahead (pace > +15): the pool is
   promoted and also serves the next class down its ranking. On pace (-10 to
   +15): normal. Behind (pace < -10) or `low` (two eligible accounts): the
   pool serves only its judgment classes. `critical` (one account) or
   `exhausted`: nothing new starts there. Eligibility comes from the LB's own
   account marks (`status`, the selector's quota_blocked),
   never from the usage fetch alone, which was blind on 2026-09-19.
2. **Capability ranking per class, best first**, in the routing table. The
   pick is the best-ranked seat whose pool's band admits the class right now,
   and `route pick` says which band decided it. Rankings: judgment (plan,
   design, hard audit, review) newest Opus > newest Sol;
   verify newest Opus / newest Sol at xhigh (cross-vendor with the author);
   implement newest Sol (gpt-implementer) > Sonnet > Opus; mechanical newest
   Luna (luna-implementer) > newest Grok on Cursor > SWE on Devin; explore and research
   Sol > Sonnet > Opus; computer Sol through Codex computer use > Opus;
   council Sol with the driver as the second voice.
3. **The newest Opus or Sol drives** and takes plan, review, design and hard
   audits. No class, seat or catch-all subagent runs on Fable.
4. **Implementation** follows the models.md ladder (2026-09-25): the cheapest
   seat that passes the piece's check, climbing one rung after two failures.
5. **Cross-vendor verification** is optional (superseded 2026-09-25): when you
   do ask for one, the verifier should not share a vendor with the author.
6. **Bands are re-read every 5 minutes** (`route alert`, or the coordinator's
   quota monitor until it lands) and before every wave a planner dispatches;
   the band line goes in the planner's 30-minute report. Reset times are
   known, so a pool that is out is scheduled for, not forgotten: work that
   wants that pool queues with the reset time on the ticket.
7. **Every dispatch and closeout is recorded** (`~/.claude/logs/dispatch.jsonl`);
   defaults change from recorded outcomes via `route learn`. A seat that is
   down is routed around by `route doctor`.

## The seats

Which seat serves which class, on which model, out of which pool, is
`config/coding-agents/routing-table.json` and nothing else — this file does not
restate it, because a second copy is a copy that goes stale.

Read it through the router rather than from memory: `route pick <class>
[--author-vendor V]` returns the first seat whose pool is live plus its
fallback chain, and `route pools` shows what is left in each pool. Classes:
`plan`, `review`, `explore`, `research`, `implement`, `mechanical`,
`verify`, `computer`, `council`.

`codex-sol`, `computer-use`, `cursor-seat`, `devin-seat`, `codex-verifier` and
`codex-test-runner` are thin forwarders: the work runs on Cursor's, Devin's and
OpenAI's quotas. `gpt-implementer`, `luna-implementer`, `gpt-explorer` and
`sol-consult` bill the Codex pool through the ccgpt bridge.
`cursor-seat` and `devin-seat` dispatch through `seat run`, which picks a healthy
registered account (`seat accounts`), fails over on a limit or auth error and
writes the receipt to the dispatch ledger; `/api/pools` serves their `cursor` and
`devin` pools from the seat state.

## Enforcement

- `hooks/seat-guard.py` (PreToolUse on Agent) denies a retired model (the
  table's `retired` list: Fable, `claude-planner`, the gpt-5.6 generation and
  older) pinned on a subagent, a subagent type whose definition pins one when
  the dispatch sets no model, and a brief that tells a forwarder to use one
  (`--model <id>`, `model: <id>`). Capacity is advisory only. Everything else
  passes and is logged.
- `hooks/subagent-closeout.py` (SubagentStop) closes the ledger line.
- `hooks/routing-pulse.py` (UserPromptSubmit) fires when a session spends 40+
  Fable requests in an hour, or 25+ in six hours with too few closeouts.
- `install-policy.py` installs the seat definitions, `routing-table.json`
  (keeping the live `overrides` that `route learn` wrote), the seat guard, the
  CLAUDE.md routing block and the settings default model (`opus`). Run
  `verify-routing` to check; it fails on any retired or older-than-newest
  model id named in a seat definition, the table, this file or the adapter.

Changing the lineup means editing this file and the routing table, not
overriding either in a session.

## Workflow stages (moved from ~/.claude/CLAUDE.md, 2026-09-25)

Alex, 2026-09-24: "dont use workflows with opus... you can have opus decide the
workflow for claude, they use a bit too many tokens." Default: Opus designs the
Workflow script and its `agent()` calls run on `model: "sonnet"`, `"haiku"` for
mechanical stages; use Opus agents where a stage needs the judgment. No hard
rule (Alex, 2026-09-25). The 09-24 limit was set while an agent-lb cache bug
made every call rewrite its context; see memory `prompt-cache-incidents-2026-09`.
Tests: always load `~/.agents/skills/test-audit/SKILL.md` (Alex, 2026-09-24)
whenever you write, change, review, cull or audit tests.
Keep context lean: the compaction window is 400k; write state to a handoff or
ledger file rather than carrying it in context, and do not poll. Dispatches and
closeouts are logged in `~/.claude/logs/dispatch.jsonl`; `route pools` shows
live headroom.
