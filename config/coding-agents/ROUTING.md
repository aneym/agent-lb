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

## Workflow quota rules (2026-10-14)

Alex, 2026-10-14 ~02:55Z: "regardless, have the factory fix this without losing quality of everything."
Alex, 2026-10-14 ~02:58Z: "ultracode on is fine - we want workflows to be used and just to have proper rules aorund it."

Full-tier tabs, including raise, recruiter, factory and rails when full, keep ultracode on. Wake-only tabs keep their current allocation. Workflow agents default to `sol-latest`. Opus runs only a judge or synthesis stage. Keep cross-vendor review and escalate failed work through the existing quality checks. Each workflow may run at most 6 agents in tight quota mode or 16 in normal mode. These limits apply to one workflow, not host job admission. `factory quota show --json` emits the rules and `route workflow-args` emits the stage options and cap.

Use scripts or Sol for wakes, status digests and polling. Never start a workflow for status, polling or single-file work. Do not wake Claude for a status digest or a routine wake. Task delivery and Alex's requests still follow the tab's tier. `factory quota tick` persists tight mode when any Claude account's 5h utilization passes 70%, retaining explicit tab allocations.

Each Workflow completion appends to `~/.open-factory/state/workflow-readback.jsonl`, or `FACTORY_HOME/workflow-readback.jsonl`. Record quota percentage from a matching workflow id in agent-lb unified utilization capture when present; otherwise record the run's tokens and label missing usage. Never infer quota percentage from token counts. Hook context for SessionStart, UserPromptSubmit and PostToolUse is at most 1024 UTF-8 bytes per hook. Deliver bulletin ids and a one-line preview, keep overflow queued, and retrieve full text with `lane-post read <id>`. Memory and learnings use short pointers with commands for full text.

## Model ladder and seats (2026-09-30; updated 2026-10-13)

Best first, and only Claude is precious; use family aliases (`route resolve <alias>`). Alex, 2026-09-30: "use things up that are best always first, using fallbacks when we need to ... the only precious thing i think is claude usage".

| Job | Seat | Model, effort |
|---|---|---|
| Code to a spec | gpt-implementer; devin-seat; sonnet-implementer | sol-latest medium; sonnet-implementer off while quota is tight (2026-10-13) |
| Mechanical sweeps | gpt-implementer; devin-seat | sol-latest low while quota is tight (2026-10-13) |
| Judgment code, or work a seat failed twice | opus-seat | off while quota is tight (2026-10-13); use sol-latest |
| Read-only exploration | gpt-explorer | sol-latest low |
| Review, on request only (non-Claude author) | verifier (Sonnet) | off while quota is tight (2026-10-13); use codex-verifier on sol-latest |
| Review, on request only (Claude author) | codex-verifier | sol-latest xhigh |
| Plan second opinion | sol-consult; astra-consult light (one per plan, brief under 2k tokens) | sol-latest high for both while quota is tight (2026-10-13) |
| Plans, specs, verdicts, design | Opus; fable-orchestrator | sol-latest for workflow agents; fable-orchestrator off while quota is tight (2026-10-13) |
| Tabs Alex talks to, leads, orchestrators | Opus tab, ultracode off; leads and orchestrators on Sol | opus-latest medium for tabs; sol-latest for leads and orchestrators |
| Remote-machine relay (batch templates) | host-relay | sol-latest low |

Available optional seat: `muse-seat` via `muse-seat-local` on Studio, Muse Code `1.4.7-R6101.1`, `muse-latest` pins `muse-wonder-1.0`. Alex reports login complete on 2026-10-13; live seat validation is pending. Boxes are not yet provisioned. No ladder promotion before interleaved evals.

- **Quota rule (2026-10-14): tabs run `opus-latest` medium; full-tier ultracode stays on.** This replaces the 2026-10-10 Fable exception and the 2026-10-12 Claude lane defaults. Alex, 2026-10-13 ~03:00Z: "okay we ahve very limited claude quota left. we need to slow thigns down a lot until we're ready - ensure everything runs on opus medium at the top level as well."
  Alex, 2026-10-13 ~03:10Z: "token allocation should be to raise and recruiter and that's basically it i think. since those are our priorities".
  Claude quota goes to the Raise and Recruiter tabs only, both at tier `full`. Every other tab (`cos`, `factory`, `rails`, `content`, `home`, `health`, `league-coach`, `frank`, `shelf`, `poker-trainer`, `internet`) is `wake-only`: no scheduled loops, timers, 2h summaries or per-bulletin wakes. These tabs wake only for prod down, a security incident, or Alex typing.
  Lanes, leads and workflow agents run Sol (`sol-latest`). Workflow judge and synthesis stages may use Opus. This quota rule overrides the ladder and older planning, wake and model defaults while quota is tight. Status goes to state files, not bulletins. The quota allocation lives in `~/.config/open-factory/quota.toml` and is managed by `factory quota`, which also emits the workflow policy.
- **Cursor is out until a passing probe at the 2026-10-30 reset.** Switch pools only on real 429s or usage-limit errors, never on a low percentage (2026-09-28).
- **While Claude is on its last account or a 5h window is under 40%,** Claude runs only tabs, plans and specs (2026-09-29).
- **Retired models run when asked** (Alex, 2026-10-05 19:55 ET: "we shouldnt just block model usage, we shoudl allow them if we request or we want t escalate things"). Each use is logged; only a model gone upstream is refused.
- **Grok and Composer never review.** Keep the Haiku background pin on Sonnet (2026-09-30).
- **Move a seat or effort level only on interleaved evals,** about 20 slices per condition, ranked by cost per accepted slice from agent-lb request logs (2026-09-28).

## Plans and slices (2026-09-26; updated 2026-10-08)

Opus plans and asks `sol-consult` before splitting complex or risky work. Every slice names its files, fixed interfaces and one check, which is one user scenario on one vertical path; the seat runs it while writing and the harness reruns it on main after merge (2026-10-07). A decision the check cannot back stays on Opus. Start independent slices together; an open product call gets a seam with a reasonable default (2026-09-28). An incident fix ships with the check that catches it next time (2026-09-26).
Tests: load the test-audit skill first, prefer e2e, then integration without mocks of our own code, then golden; never thin a test that guards a kept floor (2026-10-07).

## Capacity and placement (2026-10-05; updated 2026-10-12)

No slot counts or job caps anywhere (Alex, 2026-10-05: "we're removing slots, and going by usage and space").
- **Every seat, check, build and test suite that is not Mac-only runs on the box with the most measured room, by default, with no opt-in** (Alex, 2026-10-12 ~03:18Z: "fix this forever, like i genuinely dont understand why work isnt being sent to other places"). The seat definitions a lead's Agent call uses (gpt-implementer, gpt-explorer, sonnet-implementer, verifier, opus-seat, codex-sol, codex-verifier, codex-test-runner, computer-use) forward to `seat-run`, which places the seat on ax42, pc-wsl or forge, mirrors the worktree and brings the diff back. The main machine keeps the tabs, the merge step and Mac-only work (xcodebuild, simctl, Aside, herdr, keychain, TCC, screenshots), which uses the `<seat>-local` definitions, and runs a seat only when no box admits it.
- **Computer use never runs on Alex's desktop** (2026-10-12, R597; Alex, 2026-10-06: "we should be using isolatedcua spaces for automation in all lanes ... its interrupting me"): `computer-use` sends a `PLACEMENT: box` contract to a box, where each GUI step gets its own Xvfb display through `cua-display`; `computer-use-local` runs Mac-only GUI steps in the herdr-qa Space (a Lume VM, `herdr-shell-space`). `desktop-guard-focus` counts focus steals on Studio, and a rise during a seat's run fails it.
- **Placement is measured, not claimed** (2026-10-12): every seat placement writes one row (seat, where, host, reason, headroom) to `state/placements.jsonl`, and `placement-check` sends 20 dry-run seats through the forwarding path on every factory merge and daily; it fails when more than the Mac-only share lands on Studio.
- **Admission never sits on the merge path,** and a slice's own check runs at once in a mirror of its worktree.
- **A box dying loses a check run, never a lane:** lane state and worktrees live on the main machine and the mirror, and the check is re-placed.
- **Leads and seat forwarders run inside the lead's process,** not as new CLI sessions or panes; the seat's own work runs on the box `seat-run` picks (2026-10-12). Only the tabs Alex talks to and Mac-only steps get their own process on the main machine.
- **Back off only on real 429s, usage-limit errors or climbing error rates** (Alex, 2026-09-25: hundreds of agents must stay possible). Agent-lb throttling is a bug; report the request IDs.

## Waits, watchers and leads (2026-09-29; updated 2026-10-08)

- **Nobody writes a watcher or runs a gh poll loop** (2026-10-08): a lane waits with `factory-wait --repo <repo> --sha <sha> --until <state> --max 270` under Monitor, or is woken by the merge-events relay's lane-post task; `seat-run --wait` stays for seat results.
- **No tool call blocks past 270 s;** run long work in the background and poll in waits of 270 s or less.
- **A lead with work out sets a wake timer** before ending its turn (2026-10-05).
- **Workflow seats:** pass `agentType`, run `route workflow-args` in the launching turn and pass it as `args.route`, and use `R.review_for[<author vendor>]` only when a lead asks for a review by name (2026-10-08).
- **Agent-lb issues go to the model-ops lane** (Alex, 2026-09-25: "yes anything that is an issue with agent lb you can route to the of agent"; owner named 2026-10-13): post an agent-lb issue (quota, resets, upstream errors, routing, token audit) to lane model-ops with `lane-post`; once that lane is closed, the Factory tab (w5H:p16A) owns it.
- **Status is state, not a message** (2026-10-04); talk up the tree only for a contract change, a block outside your branch, a decision above your level, or done.
- **An orchestrator or lead first spawns the seat, then replies to Alex in one or two lines** (Alex, 2026-10-05 10:17 ET: "dont you delegate work?").
- **Reports to Alex list every open product call from each lane's `STATE.md`** (2026-10-05).
- **A done lane closes itself:** it stops its seats, leaves no wake or background shell, and ends with "the lane is closed" or "handed to <lead>".
- **Stand down an infra action with TaskStop, not SendMessage** (2026-10-09): a message reaches a seat only at its next turn, so a seat mid-action keeps going (2026-10-07: seat ax42-down sent a Hetzner hardware reset 7 min after a stand-down message). Stop the seat, then send any follow-up.

## GA restores (2026-10-08)

At launch, the rotation checklist turns the secret scan, its floor-door review and the secret guards back on, and setting `rails.mode` to `ga` brings back cross-vendor review before merge, the money-path panel, the full per-change code floor, staging before production and automatic rollback, each only if it earns its place.


- **Muse Code available seat only** (2026-10-13): `muse-seat` forwards to Studio-local `muse-seat-local` on `muse-latest`; no ladder change until interleaved evaluations. Box placement stays parked pending a scoped credential.
