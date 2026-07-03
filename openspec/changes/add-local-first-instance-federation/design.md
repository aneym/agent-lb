# Design — Instance Federation

## Guiding invariant

Anthropic rotates refresh tokens on every refresh; a reused refresh token
permanently kills the account session (`refresh_token_reused`). Therefore the
design rule for every state transition and every failure mode is:

> **Ambiguity resolves to "nobody refreshes" (temporary liveness loss), never
> to "two instances might refresh" (permanent corruption).**

Liveness loss self-heals via retry or an explicit operator force; corruption
requires re-login. All handshake ordering below follows from this rule.

## Ownership representation

- `accounts.owner_instance TEXT NULL`. `NULL` means "owned by this instance" —
  single-instance deployments never set it and behave exactly as today.
- Refresh authority == (`owner_instance IS NULL` or equals
  `settings.local_instance_id`) evaluated locally, gated at the single
  `AuthManager` choke point. There is no cross-instance runtime coordination on
  the refresh path; correctness comes from the transfer protocol alone.
- `local_instance_id`: new setting (`AGENT_LB_LOCAL_INSTANCE_ID`), default
  derived from hostname. Deliberately distinct from
  `http_responses_session_bridge_instance_id` (shared-DB pod identity — the
  opposite deployment model; do not reuse).

## Peer auth

Instance-to-instance endpoints live under `/api/federation/*` and are gated by
a dedicated shared secret (`AGENT_LB_FEDERATION_TOKEN`, bearer). When unset,
the endpoints return 403 — federation is off by default. Proxy API keys and
dashboard sessions are intentionally not reused: peer auth is a different
trust domain than client auth.

## Mirror pull (non-owner read path)

- Owner endpoint: `GET /api/federation/mirror` → for each owned account:
  account identity/provider/alias, current **access** token + expiry. Never
  the refresh token.
- Non-owner loop (scheduler pattern per `app/main.py` lifespan): upsert local
  rows with `owner_instance = <owner id>`, freshness window ~5 min, exponential
  backoff on failure. Mirrored rows are routable while the access token is
  fresh; once expired with the owner unreachable they are **excluded from
  selection** (degraded, never refreshed locally).

## Checkout (owner O → taker T), operator-driven from T

1. **Release** — T calls `POST /api/federation/checkout {account_id, taker}`
   on O. O atomically sets `owner_instance = T` (its refresh gate closes
   *now*), then returns the full auth payload (access + refresh token,
   expiries) plus a transfer nonce. O records the pending transfer nonce.
2. **Assume** — T durably imports the payload and sets its local row's
   `owner_instance = T` (= self → its gate opens). T may refresh from here on.
   Safe because O's gate closed before the tokens left O.
3. **Confirm** — T calls `POST /api/federation/checkout/confirm {nonce}` on O;
   O marks the transfer settled (bookkeeping/visibility only — authority
   already moved).

Retry semantics: Release is idempotent — if T retries and O sees
`owner_instance` already `= T` with the same pending transfer, O returns the
same payload (tokens are frozen on O since its gate closed). Lost-response
windows therefore converge. If T dies before Assume, the account is
released-but-unassumed: unroutable-after-expiry everywhere (safe direction);
recovery = T retries checkout, or operator `--force-reclaim` on O (documented
as safe only while O can verify the transfer was never confirmed **and** the
operator asserts T never refreshed).

## Checkin (T returns authority to O)

1. T sets its local `owner_instance = <O's id>` **first** (T's gate closes —
   nobody refreshes from this instant).
2. T calls `POST /api/federation/checkin {account_id, nonce, auth payload}`
   with the current (possibly rotated) tokens.
3. O imports the payload durably, then sets `owner_instance = NULL` (O's gate
   opens), responds settled.

If the response to (2) is lost, T retries; the call is idempotent under the
nonce (O answers "settled" without re-importing). T never unilaterally
reclaims after step 1 — if O is permanently unreachable, the operator
`--force-reclaim` on T requires confirming O never processed the checkin
(nonce query when reachable, human assertion otherwise). Default posture:
keep retrying; the account stays safely excluded meanwhile.

## Routing

Selection filters out accounts that are non-owned AND whose mirrored access
token is expired (insertion point: provider filter in
`load_balancer.py::_load_selection_inputs`). Owned accounts and fresh mirrors
route normally.

## Launcher preference

Candidates in order: `CLAUDE_LB_LOCAL_URL` (default `http://127.0.0.1:2455`,
short single-attempt probe — connection-refused must not burn the remote's
retry budget) → `CLAUDE_LB_BASE_URL` (existing timeout/retry semantics) →
plain claude. First healthy endpoint serves the whole session;
`CLAUDE_LB_LOCAL_PREFER=0` restores single-URL behavior.

## Corrections to proposal assumptions (from code audit 2026-07-02)

- Proactive refresh cadence is driven by the token-expiry margin check
  (`_ACCESS_TOKEN_REFRESH_MARGIN_SECONDS = 300`) under a 6h pulse interval —
  not "~90 minutes"; the corruption-on-copy conclusion is unchanged.
- SQLite leader-election short-circuit confirmed (`leader_election.py:27-29`);
  it solves single-writer-per-shared-DB and is orthogonal to federation.
