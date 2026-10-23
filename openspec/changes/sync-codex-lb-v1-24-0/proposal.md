# Change: integrate codex-lb v1.24.0 into agent-lb

## Why

agent-lb diverged from codex-lb before the stable v1.24.0 release. Bringing that
release into the fork requires reconciling its durable bridge, account ownership,
usage accounting, schema, and dashboard changes with agent-lb's provider support.

## What changes

Merge stable `Soju06/codex-lb` v1.24.0 at
`84fde5a1ed5e0d5a58ccb3ec4b82938b059bf8c5` into fork commit
`215823089aca206b5b8536d35415f9cc4a20e943`. Preserve the fork's Claude routing,
billing blocks, credential custody, team attribution, spend limits, cockpit,
and client entry points while carrying the upstream capabilities and fixes.

Join both migration histories, add durable OAuth provider identity and provider
cache counters, and align the final SQLite/PostgreSQL schema. Keep the fork's
floor/e2e test layout and deployment workflow. Add a reproducible merge-preview
command and focused integration regressions.

## Scope and readiness

The user explicitly requested one full integration PR. v1.25.0 prereleases and
upstream main are outside this change. The branch's version identifies the
imported stable release; publishing a draft does not assert release readiness.
See `verification.md` for checks, exceptions, and remaining review gates.
No running operator service or subscription database is used or changed.
