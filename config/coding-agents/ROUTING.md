# One coding-agent rules source

This is the one rules source for every machine (2026-10-05), installed at `~/.agents/policy/coding-agents/ROUTING.md`. Every machine's `~/.claude/rules/models.md` is generated from it on install; never edit local copies. Edit only the source, agent-lb `config/coding-agents/ROUTING.md`, land it on agent-lb main and run its `install-policy.py`; an edit to the installed copy is lost at the next install (2026-10-05). Current lines only: history lives in `~/.claude/ledgers/models-ledger.md`, which agents do not load.
Ask `factory ask "<question>" --json` for the current answer with live state. Record a fix with `factory learn "<problem>" "<fix>"`: other agents see it for 24 hours or until the rules change; a fix recorded twice becomes a rule (2026-10-05).
Machines come from the deterministic factory registry (`factory machines`), not markdown. Alex, 2026-10-05: "machine registry should be deterministic and not based on md files"; "rules should live on the factory home". Environment rules (browser, repo placement, machine etiquette) are in `WORKSPACE.md`, installed beside this file from the private factory repo (`rules/WORKSPACE.md`).

## Model ladder (2026-09-30; updated 2026-10-08)

Best first; only Claude is precious. Alex, 2026-09-30: "use things up that are best always first, using fallbacks when we need to ... the only precious thing i think is claude usage".
- Spec'd code: `gpt-implementer` Sol medium first, `devin-seat` SWE high overflow, Sonnet high last. Opus stays off the routine code ladder.
- Mechanical: `gpt-implementer` Sol low first, then `devin-seat` SWE medium overflow. Standard speed, never Fast; inputs under 256k.
- Ladder update (2026-10-08, audit SIMPLIFY-2026-10-07 item 9): Cursor is out until a passing probe at the 2026-10-30 reset; switch only on real 429s or usage-limit errors. Evidence: real usage-limit errors (seat-run logs remerge-5053-cursor, s13-fold3 2026-10-07; gate notes 2026-10-05).
- Read/explore: `gpt-explorer` Sol low; no decision rides on it.
- Review, after merge only (2026-10-07): Sonnet high `verifier` for non-Claude authors; `codex-verifier` Sol xhigh for Claude-authored work. Money path, migrations and gate config get the same one post-merge reviewer as any change; no panel before GA (Alex, 2026-10-07: "not treating it liek a prod app yet until we're launched"). Grok and Composer never review.
- Plans, specs, verdicts, design, build-lane tabs, job runners and fact helpers stay Opus until an eval says otherwise. Tabs Alex talks to stay Opus unless he picks Fable. Claude decides; other seats advise or type.
- Every agent tab and session runs Opus, effort medium, ultracode on (2026-10-07). Alex, 2026-10-07 19:27 ET: "for now, default all our agents to opus medium but with ultracode on please. including cc zsh command"; then: "like \"agents\" and new temrinal sessions i dispatch". That covers the orchestrator, lead, CoS, talk and Q&A tabs, the pinned agent tabs and every session cc, herdr-agent-spawn, box-session or agent-launch starts; an explicit `--model`, `--effort` or `--settings` wins, and `CLAUDE_ULTRACODE=0` turns ultracode off in the zsh launchers. Ladder seats, workflow `agent()` seats and `-p` job runners keep their own models and effort.
- Fable and Astra are readmitted for two roles (2026-10-05). Alex, 2026-10-05 19:44 ET: "right now i'm just using fable because opus has sort of failed this project. i also want you to consult with astra throughout this without burning too many tokens." `fable-orchestrator` (`fable-latest`, falling back to `opus-latest` once Fable leaves the upstream list) runs those tabs only when Alex or a dispatch asks for Fable (2026-10-07; no longer the default). `astra-consult` (`astra-latest`, high, read-only) gives plan second opinions: at most one per plan, brief under 2k tokens. Neither is on an implementer or review ladder; every other seat still treats both as retired (`readmitted` in `routing-table.json`).
- Orchestrators come first (Alex, 2026-09-29: "we cant have any of these go down, especially orchestrators like claude"). While Claude is down to its last account or a 5h window is under 40%, Claude runs lane tabs, orchestrators and Opus plans and specs only; post-merge review goes to the cross-vendor seat (2026-10-07). Explore goes to `gpt-explorer`, never `Explore`; mechanical to Sol low; non-Claude review to Sonnet `verifier`. `review_pr.py` only queues to the secret scan pre-GA and needs no model pin; no new Claude-driven eval judging or helper workflows.
- Cursor-Sonnet is unavailable until 2026-10-30; on-demand billing stays off. Re-test then with `seat run --vendor cursor --model claude-sonnet-5-5-high`. While this overflow rung is empty, the next Claude overflow rung is Opus (2026-10-05).
- Every GPT seat runs `sol-latest`, resolved at runtime (Alex, 2026-09-29: "swap all gpt routing to sol latest, 6.1 please").

## Plans, specs and defaults (2026-09-26; updated 2026-10-05)

Opus plans and asks `sol-consult` for a high-effort second opinion on complex or risky work before splitting it. Every slice names its files, fixed interfaces and one check; a seat is trusted only as far as that check reaches. Judgment code goes to `opus-seat`, as does work a seat fails twice; the runner sends a failing seat to its ALT. A decision the check cannot back stays on Opus.
Switch an exhausted pool only on real 429s or usage-limit errors, never a low percentage (2026-09-28). With Codex empty, the Claude fallback is Sonnet high, then Opus; review remains cross-vendor. Pace work both ways: off pools likely to run out before reset and onto pools that would reset unused.
How a seat earns the default (2026-09-28): rank cost per accepted slice (list-price dollars and quota share) and wall time among seats within one accepted slice of the best; unfinished escalations count against a seat. The top seat is default, then the next when its pool is empty. Move seats or effort levels only on interleaved evals, about 20 slices per condition and more than one task type. Review tokens count. Cost comes from agent-lb request_logs, not Claude Code transcripts.

## Personal project until launch (2026-10-07)

Every project is a personal project until launch (2026-10-07). Alex, 2026-10-07 21:35 ET: "remmeber, treat this as a personal project. we want fast iteration and not treating it liek a prod app yet until we're launched which is a long time from now, on every single project you see ... the only bottle neck should be raw scoping and code writing, which we fix with our good scoping system and our factory routing".
- The only bottlenecks are scoping and code writing (2026-10-07). Every step between code written and code on main is off the merge path, incremental, or under 20 s; anything slower moves after merge or goes.
- Proof is the running product and post-merge checks on main (2026-10-07); failures become the next slice.
- The kept floors stay (2026-10-07): secrets, privacy allow-lists on routine output, outbound to a real person, spend past a cap, destructive data, Alex's own credentials and sign-ins.

## Merging and review (2026-10-05; updated 2026-10-07)

Pre-launch, every project is a personal project (2026-10-07). Alex, 2026-10-07 21:35 ET: "treat this as a personal project. we want fast iteration and not treating it liek a prod app yet until we're launched ... the only bottle neck should be raw scoping and code writing". Between code written and main sits only the secret scan. Kept floors, on every repo: secrets, privacy allow-lists on routine output, outbound to a real person, spend past a cap, destructive data, Alex's own credentials and sign-ins. Everything else (review, verdicts, panels, scenarios, screenshots, design pass, parity, placement) happens after merge or not at all; findings become the next slice.
Every slice merges at once on top of the kept floors (secret scan only at merge time; see the pre-launch paragraph above, 2026-10-07). One fresh cross-vendor reviewer reads each change after merge; must-fix findings become the next slice. The fix round is after merge; nothing waits for it. Every review brief names the author vendor; no standing reviewer across slices.
Nothing after scoping needs a person (2026-10-05). Alex, 20:07 ET: "nothing after scoping shouldrequrie a person ." Once a scope is approved, agents decide and log the reason for every merge, money path, migrations, gate files and gate config included; no founder yes, no panel, no --founder flag before GA (2026-10-07; Alex, 2026-10-07 21:35 ET: "we want fast iteration and not treating it liek a prod app yet"). Only the kept floors still stop for Alex.
Restarts and cutovers need no yes (2026-10-05), agent-lb included. Alex, 20:07 ET: "remove the standing rule restart - as long as we have tech to reenable and resume connection over agent lb, and we test a sandbox version first". The condition: a sandbox or dry run of the cutover passes first, and agent-lb can re-enable the service and resume connections.
GitHub is never a blocker (2026-10-05). Alex, 20:08 ET: "if we need to roll CI ourselves, that's fine". Every machine is first class: any machine may run controller roles and message the others, and work fails over when one dies (Alex, 20:06 ET).
One slice is one vertical path and one user scenario (2026-09-28). The scenario is the slice's one check: the implementer runs it while writing, and Opus writes it. It is not a merge gate (2026-10-07): the harness reruns it on main after merge with output saved, and a failure becomes the next slice. No fails-on-base or head-only labelling before GA. Alex, 2026-10-07: "the only bottle neck should be raw scoping and code writing".
Gate code lives only in agent-rails `scripts/queue_pr.py`; harnesses never reimplement it (2026-09-28). Pre-GA its only merge check is the secret scan, plus one cross-vendor review on the secret-floor doors (`floor_doors` in `config/queue.json`) (2026-10-07). Run it with the rails-ci venv Python.
An incident fix ships with a check that catches it next time (2026-09-26). Built UI is proved after merge (2026-10-07): main deploys itself, then screenshots of the running page (light and dark, desktop and phone) and the Opus design pass follow; findings and missing platform parity become the next slice. None of it holds the merge before GA.

## Capacity and quota (2026-10-05)

No slot counts or job caps on any machine. Alex, 2026-10-05: "we're removing slots, and going by usage and space"; 2026-10-04: "blow away any job limit". All machines cut over at once (2026-10-05 09:20).
- Where agent runs, builds and batch jobs run (Alex, 2026-10-05 10:09: every job goes through placement): placement puts each one on the machine with the most measured room, and `factory-admit` on that machine admits it. A slice's own check and the tests it touches run in its worktree with no placement or admission wait, and admission never holds a merge (2026-10-07; Alex, 2026-10-07: "the only bottle neck should be raw scoping and code writing"). No machine is the default. Mac-only steps run on the Mac; whole-run pins to it are refused.
Machines admit by measured use and free space through `factory-admit`: memory, pressure, load, disk and provider holds. `factory-admit status` gives live admission facts; `factory machines` gives the registry. The Mac is one more placement candidate and keeps Mac-only steps (2026-10-05 10:09).
Once architecture is agreed, start independent slices together; only true dependencies go in order (2026-09-28). An open product call need not hold unrelated work: use a seam with a reasonable default.
Hundreds of agents must remain possible (Alex, 2026-09-25). Back off on real upstream 429s, usage-limit errors, an account under about 15% of its 5h window, or climbing error rates. `route pools` hides spent accounts; use live provider status. Agent-lb throttling is a bug; report request IDs to the usage audit owner.
Anthropic limits become an item only when the final usable account is under 15% of its week; the orchestrator monitor alerts there (2026-09-27). Design as if accounts are not the bottleneck: optimize machines, filesystem and dispatch width. Real 429s still move work.

## Seats and effort (2026-09-30; updated 2026-10-08)

| Job | Seat | Model / effort |
|---|---|---|
| Plan second opinion | sol-consult (read-only) | sol-latest high |
| Plan second opinion, light | astra-consult (read-only; one per plan, brief under 2k tokens) | astra-latest high |
| Orchestrator, lead, CoS, talk and agent tabs Alex talks to, factory decider | Opus tab (cc, ultracode on) | opus-latest medium |
| Same tabs, Fable on request | fable-orchestrator | fable-latest medium |
| Code to a spec | gpt-implementer; devin-seat overflow; Sonnet last | sol-latest medium; swe-latest high; sonnet-latest high |
| Judgment code, fallback | opus-seat | Opus |
| Read-only exploration | gpt-explorer / Explore | sol-latest low / Sonnet medium |
| Mechanical sweeps | gpt-implementer; devin-seat overflow | sol-latest low; swe-latest medium |
| Non-Claude review, after merge | verifier | Sonnet high; no panel before GA (2026-10-07) |
| Claude-authored review | codex-verifier | sol-latest xhigh |
| Remote-machine relay (batch templates only) | host-relay | Sonnet low |

Fixed effort per stage, no per-request router (2026-09-26).

| Stage | Effort | Seats |
|---|---|---|
| Relay a contract to another CLI | low | cursor-seat, devin-seat, codex-* forwarders, computer-use |
| Search and read | low–medium | gpt-explorer low, Explore medium |
| Implement to a spec | medium | interim ladder; Sonnet fallback high |
| Plan, second opinion | high | planner, plan-reviewer, sol-consult, astra-consult |
| Spec writing | medium | Opus (2026-10-01) |
| Verify, after merge | high | verifier; codex-verifier xhigh |
| Security, money path, brownfield bugs, parsers, perf, concurrency | xhigh | security-reviewer, after merge; no money-path stage before GA (2026-10-07) |
| Fully autonomous hard problem, no human in loop | max | ask first |

Open-ended work stays Opus xhigh. Lane, orchestrator and scoping tabs use medium; audit and planner tabs high (2026-10-01). Scoping tabs also launch with ultracode on, so they use workflows by default (Alex, 2026-10-05 12:06 ET); since 2026-10-07 every tab does (see the model ladder). Sonnet is not a talk or scoping default.

## Workflows, waits and leads (2026-09-29; updated 2026-10-05)

Always pass `agentType` to workflow `agent()` calls; keep seat definitions lean. A script has no shell, so run `route workflow-args` in the launching turn, pass its JSON as `args.route`, and call `agent(prompt + '\n' + R.seats[cls].brief, {...R.seats[cls].opts, label})`; review with `R.review_for[<author vendor>]` and name the author vendor in the brief (2026-10-05). Measured that day: `opts.model`/`opts.effort` override a Claude seat's frontmatter, a GPT bridge seat takes effort only from its model name (`sol-latest-high`), the Agent seat guard never sees `agent()` (only `workflow-seat-guard`, once at launch), and one workflow runs at most `agent_cap` agents at once (min(16, CPUs-2)), a forwarder holding its slot for the whole CLI run. Relaunch a Codex lane writer by resuming its thread (`codex exec resume <thread-id>`), not a cold duplicate. Keep state in files; status polling is only for jobs the seat launched.
A subagent/workflow cache lives 5 minutes, a lead's 1 hour. An agent with more work after a wait never blocks one tool call past 270 s. Run builds, tests, CI and review waits in the background (`run_in_background`, `seat-run --bg --name <n> -- <cmd>`); poll in waits of 270 s or less (Monitor timeout or `seat-run --wait <n> --max 270`). Never sleep or until-loop past 270 s in one call.
A lead with work out sets a wake timer (ScheduleWakeup or cron) before ending its turn; never rely on a finished background job to wake an idle tab (2026-10-05).
A lead stops a subagent once its report is read; the idle-agent hook reaps the rest after 20 min (2026-10-05).
A lane that is done closes its own herdr pane (Stop hook, 2026-10-05); agentless shells are swept every 10 min. Alex, 2026-10-05: "our claude chats arent closing idle lanes automatically". A done lead ends its last message with "the lane is closed" or "handed to <lead>", leaves no wake, background shell or working seat, and asks nothing.
An orchestrator or lead tab answers fast (Alex, 2026-10-05 10:17 ET: "what is taking you, as the orchestrator, so long to respond to me? dont you delegate work?"). On any request, its first action is to spawn the seat (Agent call, lane-post or build lead); it replies to Alex in one or two lines in that same turn. No diagnosis, tracing, greps or file writing in the main loop: those go to the seat. Evidence (2026-10-05): one orchestrator tab spent 2-4 min before a first action and left his queued messages unread for 8 min.
Reports to Alex list every open product call from each lane's `STATE.md`, not memory (2026-10-05). Talk up the tree only to get a decision: contract change, outside block, decision above your level or done; status is state, not a message (2026-10-04).

## Models and learning (2026-09-29; updated 2026-10-05)

Use family aliases, not versions: `opus-latest`, `sonnet-latest`, `haiku-latest`, `sol-latest`, `grok-latest`, `swe-latest`; `fable-latest` and `astra-latest` only for their readmitted seats. `route resolve <alias>` and `route models` report current models. Retired models stay off every default: no alias, pick or fallback reaches one, and a seat definition that pins one with nothing asking is denied.
Don't use `implementer`, `general-purpose` as an implementer, `luna-implementer` or `sonnet-implementer` as default implementer. Allow on request, don't block (Alex, 2026-10-05 19:55 ET: "we shouldnt just block model usage, we shoudl allow them if we request or we want t escalate things"): any retired model (Fable, Astra, Terra, gpt-5.6 and older) runs when Alex picks it, a brief or spec names it, a dispatch, `opts.model` or `route resolve <id>` names it, or an escalation asks for a stronger model; the seat guard logs each use with its reason in the routing ledger. Only an exact pin of a `blocked` model, gone upstream, is refused; a model named in a brief's prose is advice, logged, never a denial (2026-10-06). Don't use Haiku for Claude Code background calls: `ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-sonnet-5-5` stays pinned (2026-09-30).
Rules carry dates (2026-09-26). Revisit on a new model, moving accept rate, incident or Alex's steer. Older non-retired models are allowed only with eval evidence (Alex, 2026-09-29: "we can use older modlels when they're better purpose built for what we need"). Log which rung passed each task; history stays in the ledger, not this document.
The factory doctor and live dashboard exist (2026-10-05). `routing-table.json` carries executable routing; `route pick <class> --author-vendor <vendor>` returns an admitted seat and its auditor; explore is read-only and picks without one. The table is the default: `route pick <class> --prefer <rung> --effort <level> --reason <why>` runs the named rung or effort past the table's holds, never past an empty pool, a down seat or a missing reviewer, and logs it to dispatch.jsonl (2026-10-06). The seat guard denies silent retired defaults; `verify-routing` checks installs and `--source-only` checks source. `install-policy.py` installs rules and seats; never hand-edit generated local rules.
A scope that closes adds conclusions to the conclusions list (see `WORKSPACE.md`), with date and the fact that would overturn each. The weekly lookback job rechecks them against that week's Alex calls (2026-10-05).
Tests (2026-10-03; floor 2026-10-07): load the test-audit skill before writing, changing or auditing tests. Prefer e2e with test accounts, integration without mocks of our own code, then golden tests. New unit tests need a pure algorithm with many edge cases or a reviewer's must-fix. Never thin a test that guards a kept floor (secrets, privacy allow-lists, tenant isolation, outbound to a real person, spend caps, destructive data); other money-path tests are ordinary tests until GA.

## Asking Alex (2026-09-25; updated 2026-10-05)

Only these go to Alex (2026-10-05; money-path yes removed 2026-10-07): his own credentials or sign-in (a click in his own account included), spend past a cap, a message to a real person, destructive database work, going public (repo visibility, PyPI name, launch posts), and product taste inside a scope. Merges never go to him, money path included (Alex, 2026-10-07: "treat this as a personal project"). Everything after an approved scope is automatic (see Merging and review). Try CLI, API and computer use first and say what you tried. Every ask goes through unblock, never only chat. Keep working on ungated slices, park the ask and collect with `unblock_check`; never hold a lane for an answer.
