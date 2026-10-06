# Open Factory

One command routes a brief, runs it on the right CLI, and tries a stand-in when a pool is unavailable.
Python 3 and your vendor CLIs are required; there are no Python package dependencies.

## Install

From an agent-lb checkout:

```sh
clients/open-factory/install.sh
# Or: clients/open-factory/install.sh --prefix /your/bin
of doctor
```

Keep the checkout in place: installation creates symlinks. Add `~/.local/bin` to PATH if needed.
Doctor checks agent-lb, routing, launchers and stand-ins (an undeployed stand-ins endpoint is fine).
`open-factory` is the long form of `of`; both accept the same commands.

## Run a job

```sh
of run implement --cwd /path/to/repo --json -- "Rename helper x to y in a.py"
of run mechanical --intended grok-latest --brief-file brief.txt
```

Use `--author-vendor` for cross-vendor review classes and `--timeout` to bound each attempt in seconds.
Explore, research, review, verify and plan run in read-only modes. Limit or infrastructure failures
try the next seat, up to three attempts; ordinary job failures stop immediately.

## Start a factory

```sh
of start --path /path/to/repo --init-if-missing --goal "Ship the next milestone"
```

This starts the interactive Opus orchestrator using the existing factory infrastructure.

## Receipts and routing

Decisions and attempt outcomes append to `~/.claude/logs/dispatch.jsonl` (override with `ROUTE_LEDGER`).
Attempt output lives in `~/.agent-lb/of/runs/<decision_id>/attempt-<n>.txt`; JSON reports its path.
`of run --json` reports intended and actual seats, stand-ins and outcomes; exits are 0 for success,
1 for a job failure, 2 when no seat succeeds due to availability, and 4 when every seat tried was a
Cursor or Devin seat whose `seat run` reservation had to wait (try again later).
To turn ladder routing off, set `ladder: baseline` in your routing configuration; the baseline chain
still provides bounded stand-ins. `of route` keeps the optional Jev decider; `of run` uses host policy.
