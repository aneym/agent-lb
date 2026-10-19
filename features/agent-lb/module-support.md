---
id: module-support
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/__init__.py", "features/agent-lb/module-support.md"]
proofs: []
unmapped:
  - {"path": "app/modules/__init__.py", "date": "2026-10-18", "reason": "package initializer without its own route surface"}
---
# Module support

## Sub-features
The package initializer for HTTP route modules.

## How to get to it
Use the public route feature for the associated module.

## Driving it
This package file supports route imports.

## Gotchas
It is not a separately callable product surface.
