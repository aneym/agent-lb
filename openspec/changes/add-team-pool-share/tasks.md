## Implementation

- [x] Migration adds `team_members.pool_share_percent` (nullable), single head.
- [x] Pool share computation with a short shared cache, and the gate.
- [x] Dashboard API create/update/list fields and validation.
- [x] `/v1/usage` member block carries the pool share.
- [ ] Team page: drawer field and table column (separate frontend unit; not part of this branch).

## Verification

- [x] Unit tests for attribution math, window handling, gate and reset header.
- [x] Integration test through the proxy route and `/v1/usage`.
- [ ] Live: member row shows a share; `/v1/usage` with the member key returns it.
