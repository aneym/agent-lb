## Why

File-backed SQLite edges can invert the connection-pool and writer-lock order. On 2026-10-04, ax42 received 60 Codex bridge connects in 03:57:10–21Z; its first pool timeout occurred at 03:57:57Z, followed by 188 more. pc-wsl recorded 1,063 pool timeouts. Both edges remained hung after load dropped until restarted, while Postgres on Studio did not wedge.

## What Changes

- Require the writing session in `sqlite_writer_section`.
- Check out its connection before acquiring the file-backed SQLite writer lock.
- Pass the writing session at every caller, without changing writes, commits, or transaction boundaries.
- Keep Postgres and in-memory SQLite as no-ops, with no lock timeout.

## Impact

Database session helper and SQLite writer callers; no schema or external API changes.
