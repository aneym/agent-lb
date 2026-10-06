# seat run holds a capacity reservation (A4d)

## Why
The devin-seat and cursor-seat forwarders call `seat run --class <class>` directly, never a reserving launcher, so `route reserve` (A4a) did not cover them. On 2026-10-05 21:51 ET a workflow put nine jobs on Devin with one usable account; all nine sat silent for 55 minutes.

## What Changes
- `seat run --class C` without `--account` runs `route reserve C --job seat/<host>/<run_id> --prefer <seat> --reason "seat run" --json` before the dispatch row, renews the lease every `heartbeat_s` while the vendor CLI runs, and releases it `ok`, `failed` or `cancelled` (SIGTERM or Ctrl-C).
- A wait exits 4 (new) with the usual envelope, `error: "capacity: <reason>"` and `wait`; a refusal exits 2; route or agent-lb unavailable exits 1. None of them launches the vendor CLI.
- No `--class`, or a pinned `--account`, runs unreserved as today; the dispatch row says `reservation: null` and why.
- The forwarder definitions say exit 4 is a capacity wait to return, not retry.
- Fix round (review of 726658ca): the reserve comes before anything starts the vendor CLI, `cursor-agent models` included. Signal handlers only record; the run stops the CLI's process group (SIGTERM, then SIGKILL on a second signal or after 10 s), and the release runs with stop signals blocked and route in its own session, with one retry. A lease route calls gone stops the CLI and the run fails with `capacity: reservation ... was lost`. `--class verify` takes `--author-vendor` and passes it to `route reserve`; `of run` passes it through and treats a seat's exit 4 as a wait, standing in on the next seat or exiting 4.
- Fix round 2 (review of 21d53dbf): seat resolves no alias before the hold, and `route reserve` lists no vendor's models before it holds capacity: a cold model list is deferred, the rung is reserved on its alias and resolved once held (a rung that then fails, or runs another model than the caller's, is released and the walk goes on). `route reserve --model M` (with `--prefer`) reserves the preferred rung that runs M. The run uses the reserved model; a caller's `--model` that names another is refused (exit 2, nothing launched), so a verify for xAI work never runs Grok. Model listings under the hold run in their own process group and stop with the run; a stop that comes before the first attempt or during a listing ends with the cancellation envelope and 128 plus the signal. The run returns only once the vendor's whole process group is gone (SIGTERM, then SIGKILL after 10 s or on a second signal), so the release never frees capacity a descendant still uses.

## Impact
Affected specification: routing-pools. Affected code: clients/seat, clients/route (`reserve --model`, deferred discovery), config/coding-agents/agents/{cursor,devin}-seat.md.
