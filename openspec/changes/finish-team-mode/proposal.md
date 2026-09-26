# Finish team mode

## Why

The existing team-mode implementation needs end-to-end verification before deployment. Its usage cache can retain a previous calendar window, and onboarding URLs are interpolated into executable shell text without escaping.

## What changes

- Preserve the existing member CRUD, key issuance, trusted-client auth, dashboard restriction, and proxy admission behavior.
- Cache aggregates for the contracted five seconds without carrying them across a UTC window boundary.
- Quote configured onboarding URLs as literal shell values.
- Add route-level member lifecycle and migration compatibility evidence.

## Impact

Implementation and validation use the team-mode worktree. The operator subsequently authorized self-healing and the work necessary to finish the goal. Reviewed files were copied into main without modifying unrelated work, and two matching runtime Python files plus dashboard assets were selectively deployed. Team mode was enabled and the member was provisioned. See context.md for proof and remaining limitations.
