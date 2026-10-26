# Account health

Every stored account is in exactly one of three states, and they need
different fixes. Diagnose with a probe (never guess from `status` alone):

| State                       | Stored as                                                           | Probe result                                                              | Fix                                                                                                     |
| --------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| **Usable**                  | `status=active`, subscription not `canceled`                        | 2xx                                                                       | none                                                                                                    |
| **Auth'd but unsubscribed** | `status=active` + `subscription.status=canceled`                    | 403 "OAuth authentication is currently not allowed for this organization" | Human resubscribes at the provider's billing page (claude.ai/settings/billing). Reauth will NOT fix it. |
| **Disconnected**            | `status=reauth_required` or `deactivated` (deactivation reason set) | 401 / token refresh fails                                                 | Reauth with the step-4 auth scripts (same flow as adding).                                              |

A background **account pulse** (default on, every 6h, `ACCOUNT_PULSE_*` env
settings) probes every non-paused account and keeps these states truthful
automatically: it detects subscription lapses on idle accounts, marks
credential rejections `reauth_required`, and — after the human resubscribes
or reauths — restores the account to the routing pool within one cycle with
no further action. Transitions are recorded as `account_pulse_*` audit
actions. Unsubscribed and disconnected accounts stay visible in the account
list but are excluded from routing, warmup, and headline counts.

If a projections surface reports elevated `riskLevel`, it is pool-level
(mean across accounts); the `worstAccountEmail`/`worstRiskLevel` fields in
`/api/dashboard/projections` identify the single account driving it, and
`depletion*ByProvider` gives per-provider risk.

## Ongoing operations

For account-specific operations after setup — quota reset checks, stuck or
rate-limited account triage, pause/reactivate routing, billing/subscription
changes, or browser-profile work — use the `agent-lb-account-operator` skill
and the local `.agent-lb/account-profiles.json` registry. Keep one provider
account per dedicated browser profile and do not store secrets there.

Everything is API/CLI-driven — no task below ever requires the dashboard:

| Task                                    | Command                                                                        |
| --------------------------------------- | ------------------------------------------------------------------------------ |
| Restart service (e.g. after `git pull`) | `scripts/install-service.sh`                                                   |
| Stop/remove service                     | `scripts/install-service.sh --uninstall`                                       |
| Logs                                    | `~/.agent-lb/agent-lb.err.log`                                                 |
| Install/restart Claude Desktop Code proxy | `scripts/install-claude-desktop-proxy.sh`                                    |
| Claude Desktop Code proxy logs          | `~/.agent-lb/claude-desktop-proxy.err.log`                                     |
| Remove Claude Desktop Code proxy        | `scripts/install-claude-desktop-proxy.sh --uninstall`                          |
| Add / reauth an account                 | step 4 scripts                                                                 |
| List accounts                           | `scripts/anthropic-auth.sh accounts`, `scripts/openai-auth.sh accounts`        |
| Verify an account really works          | `POST /api/accounts/<id>/probe` (real upstream request; camelCase response)    |
| Re-check a canceled subscription        | `POST /api/accounts/<id>/subscription/check` (flips ledger back on success)    |
| Pause / reactivate an account           | `POST /api/accounts/<id>/pause` (or `/reactivate`)                             |
| Pool risk / projections                 | `GET /api/dashboard/projections` (pool-level risk + worst-account attribution) |
| Recent traffic per account              | `GET /api/request-logs?accountId=<id>&limit=20`                                |
| Usage summary                           | `GET /api/usage/summary`                                                       |
