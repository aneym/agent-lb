## ADDED Requirements

### Requirement: Pool share limit

A team member MAY carry a `pool_share_percent` between 0 (exclusive) and 100 (inclusive). When set, the member's usage SHALL be measured as a percentage of the total capacity of all accounts, per quota-window length, and the member SHALL be denied once any window length reaches the limit.

The share is an estimate of quota consumption, not an accounting of subscription ownership. For a window length L (minutes):

- **Reachable accounts** are the union of assigned accounts when every active member key has account-assignment scope enabled; otherwise all accounts. Accounts whose status is `deactivated` or `reauth_required` are excluded.
- **Snapshots.** For each reachable account, the latest primary and the latest secondary usage snapshot SHALL each be read independently and deduplicated by (account, `window_minutes`). A snapshot is **current** when `reset_at` is later than now and `recorded_at` is at or after `reset_at − L`.
- **Eligible accounts for L** are reachable accounts with a current snapshot of length L, plus reachable accounts whose latest snapshot of length L has a `reset_at` in the past. The latter window has rolled, so the account counts in the denominator with an attributed percent of zero. Accounts with no snapshot of length L are not eligible.
- **Attribution.** For an eligible account A with a current snapshot, the window start is `reset_at − L`. The member's attributed percent is `min(used_percent(A), 100) × member_cost(A) / total_cost(A)`. Costs are summed estimated `cost_usd` of request-log rows on A since the window start; member rows are those whose API key currently belongs to the member.
- **Null-cost rows** are imputed at the account window's priced cost per token: priced cost divided by the input plus output tokens of priced rows. If no row in the window is priced, token counts replace costs. If the total is zero, the attributed percent is zero. Until federation usage reports are available, the gate uses only this instance's logs. With priced and null-cost rows, a remote priced row could change the imputation rate, so the local attribution SHALL use the larger of the local priced-cost fraction and the local unpriced-token fraction as a conservative upper bound on the global imputed fraction. If no local row is priced, it uses the local token fraction. This upper bound only holds when all of the member's traffic goes through this one instance; members MUST use a single LB URL without fallback until federation usage reports feed the computation.
- **Share.** The member's share for L is the sum of attributed percents divided by the number of eligible accounts. If no account is eligible for any L, the share is unknown: the pool-share gate SHALL NOT deny, and fixed caps still apply.
- **Freshness.** Shares SHALL be computed from live data with at most five seconds of cache lag, SHALL NOT be stored as fixed amounts, and SHALL be computed with a bounded number of grouped queries per evaluation, not one query per account.
- **History.** Attribution follows the key's current member. Reassigning or detaching a key moves or removes its history from the member's share.

#### Scenario: A member uses part of one account

- **WHEN** two accounts share a weekly window, account A reports 40% used, the member produced a quarter of A's estimated cost since A's window started, and the member has no usage on account B
- **THEN** the member's weekly pool share SHALL be 5%

#### Scenario: The member reaches the limit

- **WHEN** a member's share for any window length is at or above its `pool_share_percent`
- **THEN** proxied requests with the member's keys SHALL receive HTTP 429 with error type `team_member_over_cap`
- **AND** `X-Team-Window` SHALL name the pool window (`pool_5h`, `pool_week`, or `pool_<minutes>m` for other lengths)
- **AND** `X-Team-Reset` SHALL be the earliest `reset_at` among eligible accounts where the member has attributed usage; it is advisory, since another window may still be over the limit

#### Scenario: A websocket session reaches the limit

- **WHEN** a member at or over its share opens a websocket to `/backend-api/codex/responses` or `/v1/responses`
- **THEN** the handshake SHALL be refused with HTTP 429 `team_member_over_cap` before the connection is accepted
- **AND** when the limit is reached on an already-open websocket, the next `response.create` SHALL fail with the same error type as a websocket error event, as fixed caps do today

#### Scenario: A stale snapshot from the previous window

- **WHEN** an account's latest weekly snapshot reports 100% with a `reset_at` that has passed
- **THEN** that account SHALL count toward the denominator with zero attributed percent, and the old 100% SHALL NOT be attributed

#### Scenario: An account window resets

- **WHEN** an account's window resets and its snapshot shows the new `reset_at`
- **THEN** the member's earlier usage on that account SHALL no longer count toward the share

### Requirement: Pool share visibility

The dashboard member list and detail SHALL return `poolSharePercent` and, per window length, the member's current share and reset time. The Team drawer SHALL let an operator set or clear the share. `/v1/usage` for a member key SHALL include the same per-window share and limit, so a member client can show how much of its allowance remains.

#### Scenario: A member key reads its own usage

- **WHEN** a member key with a pool share calls `/v1/usage`
- **THEN** the `member` block SHALL include `poolSharePercent` and one entry per pool window with `usedPercent` and `resetAt`

### Requirement: Member key beside a ChatGPT sign-in

A Codex client configured like the operator's (`/backend-api/codex`, `requires_openai_auth = true`) sends the member's own ChatGPT OAuth token as the `Authorization` bearer. HTTP and websocket proxy authentication SHALL share one token selector:

- An `sk-clb-` bearer wins.
- Otherwise, when there is no bearer or the bearer is not an `sk-clb-` key, an `sk-clb-` value in `x-api-key` is selected.
- The selected key SHALL be validated for trusted and untrusted clients alike. An invalid, revoked or expired selected key SHALL be rejected; it SHALL NOT fall back to trusted keyless access.
- A non-`sk-clb-` bearer is never validated as a proxy key.
- An `x-api-key` without the prefix does not override a present bearer, and with no bearer it keeps today's behavior.
- The member header SHALL NOT be forwarded upstream. The internal bridge SHALL NOT start trusting `x-api-key`.

#### Scenario: Codex app signed in to ChatGPT sends the member key as a header

- **WHEN** an untrusted client in team mode sends `Authorization: Bearer <ChatGPT token>` and `x-api-key: sk-clb-<member key>` to `/backend-api/codex/responses`
- **THEN** the request SHALL be admitted and attributed to the member key
- **AND** without the header the request SHALL still receive HTTP 401
