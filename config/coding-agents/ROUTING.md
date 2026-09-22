# Canonical coding-agent routing

Single source of truth for model routing on this computer. Host-neutral path:
`~/.agents/policy/coding-agents/ROUTING.md`. Host instructions and skills are
adapters; when they disagree with this file, this file wins. The history this
file used to carry (seat lineups from 2026-07 through 2026-09) is in git.

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
  family. Codex seats use `sol-latest` and `terra-latest`; Cursor seats use
  `grok-latest` (medium-fast) and `grok-latest-low`. `route resolve <alias>`
  returns the newest non-retired model actually served (the LB's model list
  for Codex, `cursor-agent --list-models` for Cursor), so a new release needs
  no edit; `route models` shows the current resolution. Exact ids stay only in
  tests, ledgers and pricing.
- **Classes.** Orchestrate, plan and drive: `opus-latest`, with `sol-latest`
  as the Codex-side alternative. Review and judgment: `opus-latest`.
  Implement means correctness-sensitive core code (balancer, routing,
  state, auth, money paths): Opus while the Anthropic general pool is on
  weekly pace in the ok band (`min_pace` -10; a low or critical pool skips
  it per rule 1, and an entry whose auditor is unavailable is skipped), else
  Codex Sol (`codex-sol`, `sol-latest`), then `terra-latest` when one is
  served. Mechanical means bounded edits with a tight spec: `grok-latest` on
  Cursor. The first A/B (2026-09-22: Opus 1/2 audit passes, Grok 0/2, both
  missing things a cross-vendor audit caught) set this split; it is re-read
  from `route report --implement` once each arm has ten or more tasks. GLM
  and Kimi are out of the chains until a seat can run them: Cursor serves
  neither. `route record` logs each implement outcome (seat, model, tokens,
  wall time, audit verdict, rework rounds). Every closeout is audited
  by the other vendor's stronger model before acceptance: `opus-latest` when
  Codex, Cursor, GLM or Kimi wrote it, `sol-latest` (`codex-verifier`, xhigh)
  when Anthropic wrote it; `route pick implement` prints the auditor.
  Mechanical: `grok-latest`. Explore and research:
  `sol-latest` (Codex, with web), `sonnet-latest`, `opus-latest`. Verify:
  cross-vendor, `sol-latest` for Anthropic authors and `opus-latest` for
  OpenAI, Cursor, GLM or Kimi authors. Computer use: `sol-latest` through
  Codex computer use (`codex exec --model "$(route resolve sol-latest)"` with
  the computer-use plugin), else `opus-latest`.
- **Top-level sessions.** `cc` and the Claude launcher start the newest Opus
  (`opus[1m]`); herdr-tab agents run Opus.
- **No gpt-6-terra today.** It is not in the upstream model catalog for these
  accounts (the LB answers 503 "no available accounts" for it), so
  `terra-latest` resolves to nothing and implementation falls through to
  `grok-latest` until a Terra ships.

## Operating rules (owner, 2026-09-22)

- **One owner per app.** If a herdr tab owns an app, no teammate runs a
  parallel lane on it.
- **Tabs coordinate directly with each other.**
- **Never ask Alex** for merges, deploys, releases that have a rehearsal and a
  backup, or infra fixes, including accounts and keys he already scoped. Do
  them and report the result. Product direction and visual sign-off go to
  Alex.
- **Every UI change is screenshot-verified.**
- **Fan out.** The driver plans and audits; independent pieces go to seats in
  parallel (up to 4 live), and the driver audits every closeout.

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
   implement Terra on Codex (when served) > newest Grok on Cursor > GLM >
   Kimi; mechanical newest Grok on Cursor > GLM > Kimi; explore and research
   Sol > Sonnet > Opus; computer Sol through Codex computer use > Opus;
   council Sol with the driver as the second voice.
3. **The newest Opus or Sol drives** and takes plan, review, design and hard
   audits. No class, seat or catch-all subagent runs on Fable.
4. **Implementation is cheap and audited.** Opus and Sol do not take volume
   implementation; the cheap seats do, and each closeout gets one cross-vendor
   audit (rule 5) by the newest Opus or Sol before it is accepted.
5. **Cross-vendor verification** stays: the verifier never shares a vendor
   with the author.
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

`implementer`, `codex-sol`, `computer-use`, `cursor-seat`, `codex-verifier`
and `codex-test-runner` are thin forwarders: the work runs on Cursor's and OpenAI's quotas, not on ours.

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
