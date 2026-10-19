---
id: module-support-limit-warmup
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/limit_warmup/", "features/agent-lb/module-support-limit-warmup.md"]
proofs: []
unmapped:
  - {"path":"app/modules/limit_warmup/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/limit_warmup/completion.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/limit_warmup/dispatch.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/limit_warmup/constants.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/limit_warmup/policy.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/limit_warmup/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/limit_warmup/anthropic_primer.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/limit_warmup/contracts.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/limit_warmup/repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Limit Warmup support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
