# seat run holds a capacity reservation (A4d)

## Why
The devin-seat and cursor-seat forwarders call `seat run --class <class>` directly, never a reserving launcher, so `route reserve` (A4a) did not cover them. On 2026-10-05 21:51 ET a workflow put nine jobs on Devin with one usable account; all nine sat silent for 55 minutes.

## What Changes
- `seat run --class C` without `--account` runs `route reserve C --job seat/<host>/<run_id> --prefer <seat> --reason "seat run" --json` before the dispatch row, renews the lease every `heartbeat_s` while the vendor CLI runs, and releases it `ok`, `failed` or `cancelled` (SIGTERM or Ctrl-C).
- A wait exits 4 (new) with the usual envelope, `error: "capacity: <reason>"` and `wait`; a refusal exits 2; route or agent-lb unavailable exits 1. None of them launches the vendor CLI.
- No `--class`, or a pinned `--account`, runs unreserved as today; the dispatch row says `reservation: null` and why.
- The forwarder definitions say exit 4 is a capacity wait to return, not retry.
- Fix round (review of 726658ca): the reserve comes before anything starts the vendor CLI, `cursor-agent models` included. Signal handlers only record; the run stops the CLI's process group (SIGTERM, then SIGKILL on a second signal or after 10 s), and the release runs with stop signals blocked and route in its own session, with one retry. A lease route calls gone stops the CLI and the run fails with `capacity: reservation ... was lost`. `--class verify` takes `--author-vendor` and passes it to `route reserve`; `of run` passes it through and treats a seat's exit 4 as a wait, standing in on the next seat or exiting 4.

## Impact
Affected specification: routing-pools. Affected code: clients/seat, config/coding-agents/agents/{cursor,devin}-seat.md.
