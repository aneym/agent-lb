# Readmit Fable and Astra for two named seats

## Why

Alex, 2026-10-05 19:44 ET: "right now i'm just using fable because opus has sort of failed this project. i also want you to consult with astra throughout this without burning too many tokens." Both families are retired, so `route resolve` cannot name them and the seat guard denies any seat that uses them.

## What Changes

- Add the `fable-latest` and `astra-latest` family aliases.
- Add a `readmitted` map to the routing table: `fable-orchestrator` may use Fable and `astra-consult` may use Astra; every other seat, alias and dispatch still treats both as retired.
- Add the two seat definitions; install-policy manages them and verify-routing checks them.
- The ccgpt bridge resolves `astra-latest[-effort]` like `sol-latest`.
- Allow on request (Alex, 2026-10-05 19:55 ET: "we shouldnt just block model usage, we shoudl allow them if we request or we want t escalate things."): a retired model named by a dispatch, brief, Workflow literal or `route resolve <id>` runs and is logged; a definition pin with nothing asking, and every alias, pick and fallback, still refuse it. A new `blocked` list is refused everywhere.

## Impact

- `clients/route`, `config/coding-agents/` (table, seats, seat guard, install-policy, verify-routing, ROUTING.md, adapter), `app/modules/proxy/api.py`.
