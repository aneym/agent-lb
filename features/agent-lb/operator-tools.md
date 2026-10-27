---
id: operator-tools
product: agent-lb
owner: weekend-finish
status: live
covers: ["cli:agent-lb", "cli:agent-lb-db", "cli:agent-lb-startup"]
paths: ["app/cli.py", "app/audit_*.py", "app/latency_cli.py", "app/db/migrate.py", "app/startup_benchmark.py", "app/recard_cli.py", "app/core/recards.py", "app/core/link_spend.py", "app/core/claude_billing.py", "app/core/cdp_page.py", "scripts/install-recard-watch.sh", "features/agent-lb/operator-tools.md"]
proofs: []
---
# Operator tools

## Sub-features
The installed agent-lb commands provide service operations, database migration, startup measurement, read-only audits, and recard: before a single-use subscription renews, `agent-lb recard run` (launchd, every 15 minutes) asks the operator to approve a Link card and then puts it on the claude.ai billing page.

## How to get to it
Install the project with uv and run `agent-lb --help`.

## Driving it
Use each command's help output to inspect supported operations and flags.

## Gotchas
Service-facing operations need the configured local service; database-changing operations affect the selected database.
