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

