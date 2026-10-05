# One coding-agent rules source

This is the one rules source for every machine (2026-10-05), installed at `~/.agents/policy/coding-agents/ROUTING.md`. Every machine's `~/.claude/rules/models.md` is generated from it on install; never edit local copies. Current lines only: history lives in `~/.claude/ledgers/models-ledger.md`, which agents do not load.
Ask `factory ask "<question>" --json` for the current answer with live state. Record a fix with `factory learn "<problem>" "<fix>"`: other agents see it for 24 hours or until the rules change; a fix recorded twice becomes a rule (2026-10-05).
Machines come from the deterministic factory registry (`factory machines`), not markdown. Alex, 2026-10-05: "machine registry should be deterministic and not based on md files"; "rules should live on the factory home". Environment rules (browser, repo placement, host etiquette) are in `WORKSPACE.md`, installed beside this file from the private factory repo (`rules/WORKSPACE.md`).

## Model ladder (2026-09-30; updated 2026-10-05)

Best first; only Claude is precious. Alex, 2026-09-30: "use things up that are best always first, using fallbacks when we need to ... the only precious thing i think is claude usage".
- Spec'd code: `cursor-seat` Grok medium, then Composer, then `gpt-implementer` Sol medium, `devin-seat` SWE high, Sonnet high last. Opus stays off the routine code ladder.
- Mechanical: `cursor-seat` Composer, then Grok low, then `devin-seat` SWE medium, then Sol low. Standard speed, never Fast; inputs under 256k.
- Read/explore: `gpt-explorer` Sol low; no decision rides on it.
- Review: Sonnet high `verifier` for non-Claude authors; `codex-verifier` Sol xhigh for Claude-authored work. Money path, migrations and gate config use an Opus panel. Grok and Composer never review.
- Plans, specs, verdicts, design, build-lane tabs, fold runners and fact helpers stay Opus until an eval says otherwise. Tabs Alex talks to stay Opus. Claude decides; other seats advise or type.
- Orchestrators come first (Alex, 2026-09-29: "we cant have any of these go down, especially orchestrators like claude"). While Claude is down to its last account or a 5h window is under 40%, Claude runs lane tabs, orchestrators and Opus plans/specs/verdicts only. Explore goes to `gpt-explorer`, never `Explore`; mechanical to Cursor; non-Claude review to Sonnet `verifier`. Use `ANTHROPIC_MODEL=claude-sonnet-5-5` for `review_pr.py`; no new Claude-driven eval judging or helper workflows.
- Cursor-Sonnet is unavailable until 2026-10-30; on-demand billing stays off. Re-test then with `seat run --vendor cursor --model claude-sonnet-5-5-high`. While this overflow rung is empty, the next Claude overflow rung is Opus (2026-10-05).
- Every GPT seat runs `sol-latest`, resolved at runtime (Alex, 2026-09-29: "swap all gpt routing to sol latest, 6.1 please").

## Plans, specs and defaults (2026-09-26; updated 2026-10-05)

Opus plans and asks `sol-consult` for a high-effort second opinion on complex or risky work before splitting it. Every piece names its files, fixed interfaces and one check; a seat is trusted only as far as that check reaches. Judgment code goes to `opus-seat`, as does work a seat fails twice; the fold sends a failing seat to its ALT. A decision the check cannot back stays on Opus.
Switch an exhausted pool only on real 429s or usage-limit errors, never a low percentage (2026-09-28). With Codex empty, the Claude fallback is Sonnet high, then Opus; review remains cross-vendor. Pace work both ways: off pools likely to run out before reset and onto pools that would reset unused.
How a seat earns the default (2026-09-28): rank cost per accepted unit (list-price dollars and quota share) and wall time among seats within one accepted unit of the best; unfinished escalations count against a seat. The top seat is default, then the next when its pool is empty. Move seats or effort levels only on interleaved evals, about 20 units per condition and more than one task type. Review tokens count. Cost comes from agent-lb request_logs, not Claude Code transcripts.

## Merging and review (2026-10-05)

Alex, 2026-10-05 09:57-09:58: cutover pieces land without review or a fix round. After cutover, the new system merges at once on top of the 2026-10-04 pre-launch floor. One fresh cross-vendor reviewer reads each change after merge; must-fix findings become the next piece. The fix round is after merge; nothing waits for it. Every review brief names the author vendor; no standing reviewer across units.
One-way doors still need Alex's yes: money-path globs (`config/money-path-paths.json`), migrations and gate config. Opus reads these diffs and the Opus panel covers them.
One piece is one vertical slice and one user scenario (2026-09-28). The scenario passes on the head, run by the harness with output saved. Opus writes it; the implementer may not edit it. Fails-on-base applies only when the runner distinguishes assertions from infrastructure failure; otherwise label it head-only or owner-boundary.
Gate code lives only in agent-rails `scripts/queue_pr.py`; harnesses never reimplement it (2026-09-28). Run it with the rails-ci venv Python.
An incident fix ships with a check that catches it next time (2026-09-26). Prove built UI with screenshots of the running page; Opus owns design and the design pass.

## Capacity and quota (2026-10-05)

No slot counts or job caps anywhere, forge included. Alex, 2026-10-05 T7: "we're removing slots, and going by usage and space"; 2026-10-04: "blow away any job limit". All hosts cut over at once (2026-10-05 09:20).
Hosts admit by measured use and free space through `factory-admit`: memory, pressure, load, disk and provider holds. `factory-admit status` gives live admission facts; `factory machines` gives the registry. Studio is one more placement candidate and keeps Mac-only steps (2026-10-05 10:09).
Once architecture is agreed, start independent pieces together; only true dependencies go in order (2026-09-28). An open product call need not hold unrelated work: use a seam with a reasonable default.
Hundreds of agents must remain possible (Alex, 2026-09-25). Back off on real upstream 429s, usage-limit errors, an account under about 15% of its 5h window, or climbing error rates. `route pools` hides spent accounts; use live provider status. Agent-lb throttling is a bug; report request IDs to usage-audit.
Anthropic limits become an item only when the final usable account is under 15% of its week; orch-watch alerts there (2026-09-27). Design as if accounts are not the bottleneck: optimize hosts, filesystem and dispatch width. Real 429s still move work.

## Seats and effort (2026-09-30; updated 2026-10-05)

| Job | Seat | Model / effort |
|---|---|---|
| Plan second opinion | sol-consult (read-only) | sol-latest high |
| Code to a spec | interim ladder; gpt-implementer | sol-latest medium |
| Judgment code, fallback | opus-seat | Opus |
| Read-only exploration | gpt-explorer / Explore | sol-latest low / Sonnet medium |
| Mechanical sweeps | cursor-seat | Composer / grok-latest low |
| Non-Claude review | verifier | Sonnet high; Opus panel for one-way doors |
| Claude-authored review | codex-verifier | sol-latest xhigh |
| Remote-host relay (batch templates only) | host-relay | Sonnet low |

Fixed effort per stage, no per-request router (2026-09-26).

| Stage | Effort | Seats |
|---|---|---|
| Relay a contract to another CLI | low | cursor-seat, devin-seat, codex-* forwarders, computer-use |
| Search and read | low–medium | gpt-explorer low, Explore medium |
| Implement to a spec | medium | interim ladder; Sonnet fallback high |
| Plan, second opinion | high | planner, plan-reviewer, sol-consult |
| Spec writing | medium | Opus (2026-10-01) |
| Verify, verdicts | high | verifier; codex-verifier xhigh |
| Security, money path, brownfield bugs, parsers, perf, concurrency | xhigh | security-reviewer; money-path verify stages |
| Fully autonomous hard problem, no human in loop | max | ask first |

Open-ended work stays Opus xhigh. Lane, orchestrator and scoping tabs use medium; audit and planner tabs high (2026-10-01). Sonnet is not a talk or scoping default.

## Workflows, waits and leads (2026-09-29; updated 2026-10-05)

Always pass `agentType` and stage effort to workflow `agent()` calls; keep seat definitions lean. Relaunch a Codex lane writer by resuming its thread (`codex exec resume <thread-id>`), not a cold duplicate. Keep state in files; status polling is only for jobs the seat launched.
A subagent/workflow cache lives 5 minutes, a lead's 1 hour. An agent with more work after a wait never blocks one tool call past 270 s. Run builds, tests, CI and review waits in the background (`run_in_background`, `seat-run --bg --name <n> -- <cmd>`); poll in waits of 270 s or less (Monitor timeout or `seat-run --wait <n> --max 270`). Never sleep or until-loop past 270 s in one call.
A lead with work out sets a wake timer (ScheduleWakeup or cron) before ending its turn; never rely on a finished background job to wake an idle tab (2026-10-05).
Reports to Alex list every open product call from each lane's `STATE.md`, not memory (2026-10-05). Talk up the tree only to get a decision: contract change, outside block, decision above your level or done; status is state, not a message (2026-10-04).

## Models and learning (2026-09-29; updated 2026-10-05)

Use family aliases, not versions: `opus-latest`, `sonnet-latest`, `haiku-latest`, `sol-latest`, `grok-latest`, `swe-latest`. `route resolve <alias>` and `route models` report current models. Retired models stay out.
Don't use `implementer`, `general-purpose` as an implementer, `luna-implementer` or `sonnet-implementer` as default implementer. Don't use gpt-5.6 or older, Astra, Fable or Terra. Don't use Haiku for Claude Code background calls: `ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-sonnet-5-5` stays pinned (2026-09-30).
Rules carry dates (2026-09-26). Revisit on a new model, moving accept rate, incident or Alex's steer. Older non-retired models are allowed only with eval evidence (Alex, 2026-09-29: "we can use older modlels when they're better purpose built for what we need"). Log which rung passed each task; history stays in the ledger, not this document.
The factory doctor and live dashboard exist (2026-10-05). `routing-table.json` carries executable routing; `route pick <class> --author-vendor <vendor>` returns an admitted seat and its auditor. The seat guard denies retired models; `verify-routing` checks installs and `--source-only` checks source. `install-policy.py` installs rules and seats; never hand-edit generated local rules.
A scope that closes adds conclusions to `~/.agent-rails/scoping/factory-program/CONTEXT/CONCLUSIONS.md`, with date and the fact that would overturn each. The weekly lookback job rechecks them against that week's Alex calls (2026-10-05 T5).
Tests (2026-10-03): load the test-audit skill before writing, changing or auditing tests. Prefer e2e with test accounts, integration without mocks of our own code, then golden tests. New unit tests need a pure algorithm with many edge cases or a reviewer's must-fix. Never thin a money-path floor.

## Asking Alex (2026-09-25)

Only real blockers go to Alex: his credential/sign-in, a click in his own account, spend/approval, a message to a person, or a product/taste call. Try CLI, API and computer use first and say what you tried. Every ask goes through unblock, never only chat. Keep working on ungated pieces, park the ask and collect with `unblock_check`; never hold a lane for an answer.
