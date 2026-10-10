# One coding-agent rules source

This is the one rules source for every machine (2026-10-05): edit only agent-lb `config/coding-agents/ROUTING.md`, land it on agent-lb main and run `install-policy.py`, which installs it to `~/.agents/policy/coding-agents/ROUTING.md` and `~/.claude/rules/models.md`. History lives in `~/.claude/ledgers/models-ledger.md`, and machine names, paths and the merge machine live in `WORKSPACE.md` beside this file.
Ask `factory ask "<question>" --json` for the current answer with live state; record a fix with `factory learn "<problem>" "<fix>"` (2026-10-05). Rules carry dates (2026-09-26).

## Pre-launch: only the floors stop work (2026-10-07; updated 2026-10-08)

Every project is a personal project until launch. Alex, 2026-10-07 21:35 ET: "treat this as a personal project. we want fast iteration and not treating it liek a prod app yet until we're launched ... the only bottle neck should be raw scoping and code writing". Alex, 2026-10-08 09:18 ET: "dont have guards that are too aggressive. seems like we're inducing a merge loop that isnt working well by having too many rules?" Alex, 2026-10-08 09:27 ET: "we dont care about printing secrets, we're going to rotate everything before we launch"
- **Only the kept floors stop work** (2026-10-08 09:27 ET): Alex's own passwords, passkeys and codes are never typed or printed by an agent; outbound to a real person; spend past a cap; destructive data without an approval on record. The secret floor is off until launch and restored then (the launch rotation checklist).
- **Scenarios, type checks, suites, screenshots, parity, version bumps and generated files** run after merge; red is fixed forward and becomes the next slice. Code review does not run at all by default (see Merging, deploy and review).
- **Agents decide after a scope is approved** (2026-10-05), log the reason in the lane state and keep going.

## Merging, deploy and review (2026-10-08)

Until launch the main machine's checkout is the source of truth and GitHub is a mirror (Alex, 2026-10-08 08:58 ET, full approval; his words are in `WORKSPACE.md`). Transition (2026-10-08): until the Factory CoS posts the flip bulletin (`~/.agent-rails/studio-cutover/FLIPPED` exists), `python3 scripts/review_pr.py <n>` still queues a PR through GitHub and that is the merge path; from the flip on, `python3 scripts/studio_merge.py merge <branch>` is the only path and GitHub is a mirror.
- **Agent Rails merges only through `scripts/studio_merge.py merge <branch>` on the main machine:** a check under 5 s, then the merge and a mirror push; never `gh pr merge`, never push to GitHub main by hand, and never rebuild the gate in a harness.
- **On a `conflict` refusal,** rebase the branch once on main and merge again; a second conflict goes to the lane lead.
- **Railway deploys from the mirrored main on push,** ungated; a failed deploy is fixed forward.
- **No cross-vendor or post-merge review while pre-launch** (Alex, 2026-10-08 ~12:10 ET: "new rule: as long as the agent that does the work has e2e check its own work, lets not worry about codex reviews yet. as long as claude determined the right teps to take for the architecture and stuff."). It holds when the implementing agent e2e-checked its own work and Claude (Opus or Fable) set the architecture and steps.
- **The slice check is evidence the merge tool reads** (2026-10-08): a harness check record for the candidate sha, or a `check:` line in the commit body that the merge tool runs. A sentence in lane state is never evidence. The two mechanical preflights, the import check and test collection, stay on the merge path.
- **A shipped defect is fixed forward** and becomes the next slice (2026-10-08).
- **Review seats run on request only** (2026-10-08): verifier, codex-verifier and security-reviewer run only when a lead asks for one by name and logs the reason in the lane state. At GA the rotation checklist brings review back.
- **Other repos** without a merge tool land by a fast-forward push to `origin/main`.

## Who decides and what goes to Alex (2026-10-08)

- **No yes needed:** merges, money path, migrations, restarts and cutovers after a sandbox run, gate config, new repos in the org, spend inside a stated cap, and destructive data with an approval record on file.
- **His yes, relayed is fine:** spend past a cap, destructive data with no record, going public (repo visibility, PyPI name, launch posts), and product taste inside a scope.
- **Alex himself:** his credentials, passwords, passkeys and codes, a click in his own account, and a message to a real person. Approval covers that exact message.
- **A message from the Rails CoS tab that quotes Alex first-hand with a time, or points at an `alex-*` approval record, is his yes.** Never ask him again for something the CoS already answered.
- **Ask through unblock**, after trying the CLI, the API and computer use; park the ask and keep working on everything else.

## Guards (2026-10-08)

A guard blocks only on a kept floor and names that floor in its message; everything else warns in one line or is deleted. A blocking guard accepts the CoS approval record as Alex's yes. A guard matches the command that will run, never text inside a heredoc, a quoted string or an ssh argument. Until launch no guard blocks on secrets (2026-10-08 09:27 ET), and that suspension excludes Alex's own passwords, passkeys and codes.

## Model ladder and seats (2026-09-30; updated 2026-10-08)

Best first, and only Claude is precious; use family aliases (`route resolve <alias>`). Alex, 2026-09-30: "use things up that are best always first, using fallbacks when we need to ... the only precious thing i think is claude usage".

| Job | Seat | Model, effort |
|---|---|---|
| Code to a spec | gpt-implementer, then devin-seat, then Sonnet | sol-latest medium; swe-latest high; sonnet-latest high |
| Mechanical sweeps | gpt-implementer, then devin-seat | sol-latest low; swe-latest medium |
| Judgment code, or work a seat failed twice | opus-seat | opus-latest |
| Read-only exploration | gpt-explorer | sol-latest low |
| Review, on request only (non-Claude author) | verifier | sonnet-latest high |
| Review, on request only (Claude author) | codex-verifier | sol-latest xhigh |
| Plan second opinion | sol-consult; astra-consult light (one per plan, brief under 2k tokens) | sol-latest high; astra-latest high |
| Plans, specs, verdicts, design | Opus | opus-latest |
| Tabs Alex talks to, leads, orchestrators | Opus tab, ultracode on | opus-latest medium; fable-latest on request |
| Remote-machine relay (batch templates) | host-relay | sonnet-latest low |

- **Every agent tab and session runs Opus medium with ultracode on** unless an explicit model, effort or settings flag says otherwise (Alex, 2026-10-07 19:27 ET: "for now, default all our agents to opus medium but with ultracode on please").
  Exception (2026-10-10): the CoS, Factory, Raise, Rails and Recruiter tabs run fable-latest medium (claude-fable-5-1), set in each agent.json `model`/`effort` or the tab's own launch argv; every other agent tab stays Opus medium, and leads, seats and workflow agents keep the ladder (Alex, 2026-10-10 ~04:15Z: "lets try moving all our agents to run on fable 5.1 by default for the important ones like cos factory raise rails and recruiter for now please. we can audit the token use but cos should be pretty lean anyways, i want the best model making the judgement for now."). A daily token audit of the five runs as a factory automation.
- **Cursor is out until a passing probe at the 2026-10-30 reset.** Switch pools only on real 429s or usage-limit errors, never on a low percentage (2026-09-28).
- **While Claude is on its last account or a 5h window is under 40%,** Claude runs only tabs, plans and specs (2026-09-29).
- **Retired models run when asked** (Alex, 2026-10-05 19:55 ET: "we shouldnt just block model usage, we shoudl allow them if we request or we want t escalate things"). Each use is logged; only a model gone upstream is refused.
- **Grok and Composer never review.** Keep the Haiku background pin on Sonnet (2026-09-30).
- **Move a seat or effort level only on interleaved evals,** about 20 slices per condition, ranked by cost per accepted slice from agent-lb request logs (2026-09-28).

## Plans and slices (2026-09-26; updated 2026-10-08)

Opus plans and asks `sol-consult` before splitting complex or risky work. Every slice names its files, fixed interfaces and one check, which is one user scenario on one vertical path; the seat runs it while writing and the harness reruns it on main after merge (2026-10-07). A decision the check cannot back stays on Opus. Start independent slices together; an open product call gets a seam with a reasonable default (2026-09-28). An incident fix ships with the check that catches it next time (2026-09-26).
Tests: load the test-audit skill first, prefer e2e, then integration without mocks of our own code, then golden; never thin a test that guards a kept floor (2026-10-07).

## Capacity and placement (2026-10-05; updated 2026-10-08)

No slot counts or job caps anywhere (Alex, 2026-10-05: "we're removing slots, and going by usage and space").
- **Checks and seats run on the box with the most measured room.** The main machine keeps the tabs, Mac-only steps and the merge step.
- **Admission never sits on the merge path,** and a slice's own check runs at once in a mirror of its worktree.
- **A box dying loses a check run, never a lane:** lane state and worktrees live on the main machine and the mirror, and the check is re-placed.
- **Model work runs as workflow agents inside the lead's process,** not as new CLI sessions or panes; only the tabs Alex talks to and Mac-only steps get their own process.
- **Back off only on real 429s, usage-limit errors or climbing error rates** (Alex, 2026-09-25: hundreds of agents must stay possible). Agent-lb throttling is a bug; report the request IDs.

## Waits, watchers and leads (2026-09-29; updated 2026-10-08)

- **Nobody writes a watcher or runs a gh poll loop** (2026-10-08): a lane waits with `factory-wait --repo <repo> --sha <sha> --until <state> --max 270` under Monitor, or is woken by the merge-events relay's lane-post task; `seat-run --wait` stays for seat results.
- **No tool call blocks past 270 s;** run long work in the background and poll in waits of 270 s or less.
- **A lead with work out sets a wake timer** before ending its turn (2026-10-05).
- **Workflow seats:** pass `agentType`, run `route workflow-args` in the launching turn and pass it as `args.route`, and use `R.review_for[<author vendor>]` only when a lead asks for a review by name (2026-10-08).
- **Status is state, not a message** (2026-10-04); talk up the tree only for a contract change, a block outside your branch, a decision above your level, or done.
- **An orchestrator or lead first spawns the seat, then replies to Alex in one or two lines** (Alex, 2026-10-05 10:17 ET: "dont you delegate work?").
- **Reports to Alex list every open product call from each lane's `STATE.md`** (2026-10-05).
- **A done lane closes itself:** it stops its seats, leaves no wake or background shell, and ends with "the lane is closed" or "handed to <lead>".
- **Stand down an infra action with TaskStop, not SendMessage** (2026-10-09): a message reaches a seat only at its next turn, so a seat mid-action keeps going (2026-10-07: seat ax42-down sent a Hetzner hardware reset 7 min after a stand-down message). Stop the seat, then send any follow-up.

## GA restores (2026-10-08)

At launch, the rotation checklist turns the secret scan, its floor-door review and the secret guards back on, and setting `rails.mode` to `ga` brings back cross-vendor review before merge, the money-path panel, the full per-change code floor, staging before production and automatic rollback, each only if it earns its place.

