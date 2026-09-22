# Canonical coding-agent routing

Single source of truth for model routing on this computer. Host-neutral path:
`~/.agents/policy/coding-agents/ROUTING.md`. Host instructions and skills are
adapters; when they disagree with this file, this file wins. The history this
file used to carry (seat lineups from 2026-07 through 2026-09) is in git.

## The rule (owner, 2026-09-20: adaptive; supersedes the 2026-09-19 ration)

Every pool is a weekly budget with a known reset, and what is unspent at the
reset is lost. So the router does two things at once: spend every pool on pace
to its reset, and put the strongest seat that pace allows on each class.

1. **Pace, not headroom, decides.** For each pool: `pace = remaining% -
   (hours_to_reset / hours_in_cycle x 100)`. Ahead (pace > +15): the pool is
   promoted and also serves the next class down its ranking. On pace (-10 to
   +15): normal. Behind (pace < -10) or `low` (two eligible accounts): the
   pool serves only its judgment classes. `critical` (one account) or
   `exhausted`: nothing new starts there. Eligibility comes from the LB's own
   account marks (`status`, the selector's quota_blocked, the Fable marker),
   never from the usage fetch alone, which was blind on 2026-09-19.
2. **Capability ranking per class, best first**, in the routing table. The
   pick is the best-ranked seat whose pool's band admits the class right now,
   and `route pick` says which band decided it. Rankings: judgment (plan,
   design, hard audit, review) Fable > Opus > Astra; verify Opus / Astra
   (cross-vendor with the author) > Sol-xhigh on Cursor; implement Opus =
   Astra > Sol-high on Cursor > GLM; mechanical Grok 4.6 on Cursor > GLM >
   Kimi > Opus; explore Sonnet > Grok-low > Opus; computer Astra; council and
   research Astra with Fable as the second voice.
3. **Fable is always the driver** and takes design and hard audits. With two
   Fable-eligible accounts live and Fable ahead of pace, it also takes the
   review and hard-verify classes; it never takes volume implementation, and no
   catch-all subagent inherits it.
4. **Opus is the default builder and verifier while general is ok.** When
   general goes low, Astra and Sol take implement and mechanical, and Opus keeps
   one cross-vendor read per closeout.
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
`plan` (the driver keeps it), `review`, `explore`, `implement`, `mechanical`,
`verify`, `computer`.

`cursor-seat` and `codex-verifier` are thin forwarders: the work runs on
Cursor's and OpenAI's quotas, not on ours. Never a `claude-fable-*` model on
Cursor (no ZDR agreement).

## Enforcement

- `hooks/seat-guard.py` (PreToolUse on Agent) denies exactly two shapes: a
  `claude-fable-*` model pinned on a subagent, and a catch-all `subagent_type`
  with no model. Opus and everything else pass. `fork` passes, logged.
- `hooks/subagent-closeout.py` (SubagentStop) closes the ledger line.
- `hooks/routing-pulse.py` (UserPromptSubmit) fires when a session spends 40+
  Fable requests in an hour, or 25+ in six hours with too few closeouts.
- `install-policy.py` installs the seats, the hooks, the settings entries and
  the `com.aneyman.route-doctor` launchd job. Run `verify-routing` to check.

Changing the lineup means editing this file and the routing table, not
overriding either in a session.
