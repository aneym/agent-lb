## Implementation

- [ ] Migration adds `team_members.pool_share_percent` (nullable), single head.
- [ ] Pool share computation with a short shared cache, and the gate.
- [ ] Dashboard API create/update/list fields and validation.
- [ ] `/v1/usage` member block carries the pool share.
- [ ] Team page: drawer field and table column.

## Verification

- [ ] Unit tests for attribution math, window handling, gate and reset header.
- [ ] Integration test through the proxy route and `/v1/usage`.
- [ ] Live: member row shows a share; `/v1/usage` with the member key returns it.
