## Context

OAuth providers can rotate refresh tokens on every successful exchange. The service already coalesces identical in-process refresh attempts, but refreshes can still overlap across database sessions or processes. A losing attempt can receive a permanent provider error after another attempt has stored a valid replacement token.

The current permanent-error reconciliation reads through SQLAlchemy's identity map, which can return the same stale account object rather than current database state. Token persistence is also unconditional, so a late writer can overwrite newer credentials. Both behaviors violate the account owner's expectation that the newest valid credential version wins.

## Goals / Non-Goals

**Goals:**

- Prevent a stale refresh failure from marking an account `reauth_required` after another refresh stored newer credentials.
- Prevent a stale successful refresh from overwriting newer stored token material.
- Preserve existing provider behavior, account APIs, and in-process refresh coalescing.
- Cover the race at both the authentication-manager and repository boundaries.

**Non-Goals:**

- Coordinate refreshes with an external distributed lock.
- Change provider token exchange semantics or public account endpoints.
- Recover credentials after the provider has genuinely invalidated the only stored refresh token.

## Decisions

### Force database refresh before a permanent status transition

The repository will expose an explicit reload operation using SQLAlchemy's `populate_existing` behavior. Permanent refresh failures will compare their original token version with this freshly loaded row before changing account status. A normal `get_by_id` is insufficient because it may resolve from the session identity map.

An alternative was expiring the entire session before every read. That is broader than necessary and makes unrelated loaded entities harder to reason about.

### Use compare-and-swap for token persistence

Token updates will optionally require the stored encrypted refresh token to equal the exact encrypted value observed before the provider exchange. Encryption output itself is stable for the stored version, so exact ciphertext is a suitable opaque version marker without decrypting or logging credentials.

If the conditional write loses and a forced reload shows different refresh-token material, the refresh converges on that newer row. If no newer version exists, the operation fails rather than silently claiming success.

An alternative was a database advisory lock. Compare-and-swap is smaller, does not hold a database lock during a network call, and directly protects the write invariant.

### Roll back a lost conditional write

The repository will suppress autoflush during the conditional statement and roll back when no row matches. This prevents mutations on a session-bound stale account object from being flushed during conflict handling.

## Risks / Trade-offs

- **Risk: Encrypted-token equality becomes an implicit version field.** → Keep the comparison internal and opaque; a future schema migration can replace it with an explicit credential-version column without changing the behavior contract.
- **Risk: A real permanent failure can be deferred when another writer changed credentials.** → The newer credentials remain eligible for the next probe or refresh and will be marked only if that current version also fails permanently.
- **Risk: A provider exchange succeeds but its write loses.** → Prefer the already stored newer version and never overwrite it with the stale result.

## Migration Plan

No schema or data migration is required. Deploy after unit and repository regression tests pass, restart the local service, and probe the recovered account through its pinned account endpoint. Rollback is a code revert; stored credential data remains compatible.

## Open Questions

None.
