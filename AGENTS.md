# Agent LB project facts

- Setup reference: `GETTING-STARTED.md`. API and behavior contracts: `openspec/specs/`. Contribution rules for upstream PRs: `.github/CONTRIBUTING.md`.
- Development Python is `.venv/bin/python`; the project uses uv. Relevant local checks include `uv run pytest` and `uv run ruff check app clients`.
- The live service runs from `~/.agent-lb/runtime/agent-lb`, not this external-disk checkout. Source edits alone do not deploy it.
- The existing source deployment helper is `~/.agent-lb/bin/sync-runtime.sh`. Its exit code alone is not proof of a successful copy; verify the reviewed hashes and sync log. A failed sync may be partial. Service changes need an authorized, coordinated window.
- Launchd paths stay on the internal disk because external-volume scans previously terminated the service family. Restarting the main service does not automatically update satellite copies.
- Account totals distinguish authenticated, subscription-usable accounts from all stored accounts.
- File-pinned requests cannot fail over across accounts. Reservation settlement, health updates, cancellation, and finalization ordering are correctness constraints, including on partial failure.
- Concurrent tasks cannot share one SQLAlchemy `AsyncSession`. Bound fan-out and preserve ownership of spawned tasks.
- Alembic migrations preserve the intended single-head graph and existing-row compatibility.
- `CHANGELOG.md` is maintained by the release process. Specifications and rationale have separate `spec.md` and `context.md` files.
