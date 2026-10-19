---
id: operator-tools
product: agent-lb
owner: weekend-finish
status: live
covers: ["cli:agent-lb", "cli:agent-lb-db", "cli:agent-lb-startup"]
paths: ["app/cli.py", "app/audit_*.py", "app/latency_cli.py", "app/db/migrate.py", "app/startup_benchmark.py", "features/agent-lb/operator-tools.md"]
proofs: []
---
# Operator tools

## Sub-features
The installed agent-lb commands provide service operations, database migration, startup measurement and read-only audits.

## How to get to it
Install the project with uv and run `agent-lb --help`.

## Driving it
Use each command's help output to inspect supported operations and flags.

## Gotchas
Service-facing operations need the configured local service; database-changing operations affect the selected database.
