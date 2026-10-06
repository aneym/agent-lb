## Why

After an unclean reboot, database recovery outlasted runtime startup and watchdog retries. Boot logging must not expose credentials, probes must remain bounded even during DNS stalls, and an unknown database configuration must not authorize a restart.

## What Changes

- Wait for PostgreSQL at boot within the configured budget (900 seconds by default), with an eight-second backoff ceiling and bounded probes, then start the runtime even if the budget expires.
- Parse only the database host and port for probe arguments and logs; reject unparseable URLs without logging their contents.
- Fail closed on watchdog configuration lookup failures and unavailable database probes.
- Keep usage-refresh cooldown jitter inside the existing 900-second ceiling.

## Impact

Runtime launcher, watchdog and usage-refresh cooldown. No schema, credentials, deployment or request-payload changes.
