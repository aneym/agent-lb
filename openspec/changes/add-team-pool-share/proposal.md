# Team member pool share

## Why

Team members are capped only by fixed dollar and token amounts per UTC day, week and month. The dollar figures are list-price estimates, not what the operator pays: the pool is subscriptions whose real limit is each account's quota window. On 2026-09-20 the operator's brother was given "25% of the pool" by computing 25% of that day's pool headroom in dollars and typing the result in as fixed caps. Nothing recomputes it, so the number is meaningless a week later. The operator wants a member's allowance expressed only as a percentage of the total capacity of their accounts. A member scoped to a subset (for example Codex only) is measured against the accounts that member can reach.

## What changes

- Add a nullable `pool_share_percent` (0 < p ≤ 100) to team members.
- Measure a member's pool share per quota-window length: for each active account, attribute the account's reported `used_percent` to the member in proportion to the member's share of that account's estimated cost since the account's window started; sum and divide by the number of accounts that have that window.
- Gate: when set, a member whose share of any window length reaches the limit gets HTTP 429 `team_member_over_cap`, with `X-Team-Window` naming the pool window and `X-Team-Reset` at the soonest reset among the accounts that carry the member's usage.
- Expose the share in the dashboard (member list, drawer field) and in `/v1/usage` for the member's own key.
- Accept a member key in `x-api-key` when the bearer is a ChatGPT token, so the Codex desktop app can keep its ChatGPT sign-in and the Codex-shaped `/backend-api/codex` routes (model list included) while billing the member key.
- Fixed dollar and token caps stay available but are optional; the operator's member moves to a pool share only.

## Impact

`app/modules/team/**`, `app/modules/proxy/api.py` and `schemas.py` (`/v1/usage` member block), one Alembic migration, the Team dashboard feature, the Windows tray client outside the repo. No routing change.
