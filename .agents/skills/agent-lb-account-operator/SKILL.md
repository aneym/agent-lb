---
name: "agent-lb-account-operator"
description: |
  Manage agent-lb account subscription/admin work with local browser profiles,
  the dashboard subscription ledger, and gitignored operator memory.
metadata:
  author: agent-lb
  version: "1.0.0"
---

# Agent LB Account Operator

Use this skill when the user asks to add, cancel, pause, reactivate, verify, or
open an OpenAI/ChatGPT/Anthropic account that agent-lb routes through. Also use
it for account-specific support work such as quota reset checks,
stuck/rate-limited account triage, subscription/account status checks, routing
imbalance reports, or pause/reactivate routing requests.

If the user is setting up a fresh machine or wiring clients, use `get-started`
first. Use this skill for account-specific browser profiles, billing/admin
actions, subscription-ledger updates, account status diagnostics, quota/rate
limit triage, routing pause/reactivate work, routing imbalance diagnostics,
account removal, or verification.

## Add or upgrade an account

Follow [the account enrollment workflow](#enroll-a-subscription-account) end to end.
It covers verified browser identity, Claude purchase with a Link temporary card,
phone verification, OAuth enrollment, subscription recording and a real upstream
probe. A paid website account is not connected to Agent LB until OAuth completes.

## Local registries

Use `agent-lb profiles list --json` for the live browser registry. It is stored at
`~/.agent-lb/state/browser-profiles.json` (or `AGENT_LB_STATE_DIR`). Its `email`,
`dir`, `providers` and `cdpPort` fields are authoritative for opening the browser.
The directory can differ from the email. Never reconstruct it from the email or
assume a port belongs to the account its old window used.

`.agent-lb/account-profiles.json` is optional gitignored operator history, not the
browser launcher registry. Read matching notes when present; never commit account
emails, local profile records or credentials. The app database is authoritative
for routing state and the subscription ledger.

## Operating model

1. Match the account by provider and full email; infer the provider from the
   current task when it is already clear.
2. Open its registered profile with `agent-lb profiles open EMAIL`; attach browser
   automation to the returned registry's CDP port and verify the website identity.
3. For new registrations, use `agent-lb profiles add EMAIL --provider anthropic`
   (or `openai`), preserving an existing directory unless moving it is intended.
4. Complete the authorized task without re-asking for routine account clicks.
   Ask only when sign-in, phone verification or payment authorization really
   requires the human. Never expose passwords, codes or payment credentials.
5. Update `agent-lb subscriptions set` after verified billing actions. Record dates,
   amount, renewal and temporary-card limitations, not just the plan name.
6. Confirm live upstream behavior before reporting ready. For stuck or
   rate-limited accounts, inspect the account row and matching notes before
   recommending a purchase or reauthentication.

## Auth Verification (real upstream probes)

To verify that an account's auth actually works — not just that tokens are
stored — use the dashboard API's per-account probe endpoints on the local
service (`http://127.0.0.1:2455`, or the tailnet serve URL). Both send a real
minimal `/v1/messages` request pinned to that one account, refreshing tokens
first and bypassing load-balancer scoring:

- `POST /api/accounts/{accountId}/probe` — returns `probeStatusCode` plus
  before/after usage percents and account status. Also wakes the upstream
  rate limiter for stuck accounts and forces a usage refresh.
- `POST /api/accounts/{accountId}/subscription/check` — same probe, but also
  returns the upstream error `message` and updates the subscription ledger
  (`working`, `subscription.status`, `subscription.notes`). Run this second,
  when a probe fails, to see why.

Interpretation and gotchas:

- Responses are camelCase (`probeStatusCode`, `accountStatusAfter`), not
  snake_case.
- `200` = auth and subscription both usable.
- `403` with "OAuth authentication is currently not allowed for this
  organization" = tokens are fine but the org has no active Claude
  subscription (canceled/lapsed). Billing problem, not an auth problem —
  reauth will not fix it; resubscribe or remove instead.
- `401` = auth actually broken; reauth via the account's dedicated browser
  profile.
- Accounts in `paused` / `reauth_required` / `deactivated` state are refused
  with HTTP 409 `account_not_probable`; reactivate first if a probe is needed.
- Anthropic probes default to `claude-haiku-4-5` with `max_tokens: 4`
  (negligible quota). Override with `{"model": "..."}` in the request body.

Verify-all loop (swap the provider filter as needed):

```bash
LB=http://127.0.0.1:2455
curl -s "$LB/api/accounts" |
  jq -r '.accounts[] | select(.provider=="anthropic") | "\(.accountId)|\(.email)|\(.status)"' |
  while IFS='|' read -r id email st; do
    [ "$st" = active ] || { echo "$email: skipped ($st)"; continue; }
    curl -s -X POST "$LB/api/accounts/$id/probe" -H 'content-type: application/json' -d '{}' |
      jq -c --arg e "$email" '{email: $e, probeStatusCode, accountStatusAfter}'
  done
```

## Account State Model (and which fix to recommend)

Every stored account is in exactly one of three states. Diagnose with a probe;
never infer the fix from `status` alone:

- **Usable** — `status=active`, subscription ledger not `canceled`, probe 2xx.
- **Auth'd but unsubscribed** — `status=active` + `subscription.status=canceled`,
  probe 403 with the org-OAuth-refused message. Credentials are fine; the org's
  subscription lapsed. Fix = the human resubscribes at the provider billing page
  (open their dedicated browser profile). Reauth does nothing here.
- **Disconnected** — `status=reauth_required`/`deactivated` with a deactivation
  reason, probe 401 or token refresh fails. Fix = reauth via the repo's auth
  scripts (`scripts/anthropic-auth.sh start` / `scripts/openai-auth.sh start`,
  same flow as adding an account — no dashboard needed).

A background **account pulse** (default on, 6h interval, `ACCOUNT_PULSE_*` env
settings) probes every non-paused account and reconciles these states
automatically — including restoring accounts to the routing pool within one
cycle after the human resubscribes or reauths, and correcting stale
misdiagnoses (e.g. a deactivated "auth failed" account that is actually just
unsubscribed). Pulse transitions appear as `account_pulse_*` audit actions.
After a human billing/reauth action, you can shortcut the wait with
`POST /api/accounts/{id}/subscription/check` (canceled ledgers only) or a
probe. Unsubscribed and disconnected accounts stay visible in `/api/accounts`
but are excluded from routing, warmup, and headline counts.

When someone asks why a projections surface says elevated risk:
`GET /api/dashboard/projections` risk is pool-level (mean across accounts);
`worstAccountEmail`/`worstRiskLevel` name the account driving it and
`depletionPrimaryByProvider`/`depletionSecondaryByProvider` scope it per
provider. A single hot account (e.g. one long interactive session pinned by
sticky routing) elevates its own attribution, not the pool.

## Browser profiles

Use `agent-lb profiles list --json`, `profiles add` and `profiles open` rather than
handwritten Chrome commands. `agent-lb reauth EMAIL --provider PROVIDER --dry-run`
shows the exact profile path, CDP port and OAuth plan without starting a login.

For standard websites and embedded Stripe fields use the available browser agent
or direct Playwright CDP. Argent is not required by this account workflow; prefer
the tool that can operate the actual form. Do not change browser tools repeatedly
when one already reaches the page. Keep the requested account profile open and
preserve unrelated sessions unless the user requested their closure.

## Subscription Ledger Mapping

Use these statuses in the agent-lb dashboard/API ledger:

- `active`: subscription is expected to renew on `nextChargeAt`.
- `pause_pending`: operator intends to pause/cancel before `nextChargeAt`, but
  vendor action is not yet complete.
- `paused`: vendor subscription or local routing should be treated as paused;
  check notes for which one.
- `canceled`: vendor access period ended or subscription is fully canceled.

For screenshots like "will remain active until ... Jun 22, 2026", keep the
subscription active and record the date separately:

```json
{
  "status": "active",
  "currentPeriodEndAt": "2026-06-22T04:00:00.000Z",
  "nextChargeAt": null
}
```

## Safety

- Do not store passwords, recovery codes, access tokens, refresh tokens, card
  numbers, or secrets in the registry.
- A user request to buy, add, upgrade or connect an account authorizes its scoped
  routine steps. Carry that authorization forward; do not demand confirmation
  again each turn. Honor spend caps and any payment-provider approval requirement.
- Distinguish vendor subscription state from agent-lb routing state. Pausing an
  account in agent-lb only removes it from routing; it does not cancel vendor
  billing.

## Enroll a subscription account

The goal is a working account in the routing pool, not just a paid website login.
Use this path for an authorized Claude account purchase and enrollment; skip the
billing steps for an already-paid account. For OpenAI, use the same identity and
readiness checks with `--provider openai`.

## 1. Resolve the actual browser profile

```bash
agent-lb profiles list --json
agent-lb reauth EMAIL --provider anthropic --dry-run
```

If no profile exists, register one with `agent-lb profiles add EMAIL --provider
anthropic`. Otherwise retain the existing `dir` and `cdpPort`, even when the
directory name is another email. Open with `agent-lb profiles open EMAIL`, attach
the browser agent to that CDP port and navigate to `https://claude.ai/settings/billing`.
Verify the full signed-in email and current plan in the rendered page. A named
folder, existing process or stored cookie does not prove account identity or login.
Never create a second empty profile by deriving a path from the email.

Prefer an available browser agent or direct Playwright CDP for normal websites.
Use DOM locators inside Stripe frames when the browser tool cannot reach embedded
fields. A failed DOM lookup calls for inspecting that frame's controls, not a
30-second retry of an assumed selector. Never dump cookies, token values, form
values or an entire browser state into output.

If already signed in, continue. If sign-in is required, use an approved secure
credential reference without bringing plaintext into the transcript. Do not
assume the account password is available. Let the human perform password/2FA
steps that cannot be handled by the authorized secure flow. Keep the same profile
through billing and OAuth.

## 2. Verify billing before paying

Read the actual billing page. If the requested plan is already active, do not
purchase again. If it shows Free, choose Max 20x when that is the requested plan,
fill the authorized billing address, then verify the final total including tax.
Accept necessary account terms; leave optional model-training consent off unless
requested otherwise.

Use the installed payment-credential skill and live `link-cli --help` for current
CLI syntax. Check `link-cli auth status` and `link-cli spend-request list --format
json`. Retrieve relevant existing requests before creating another one. Distinguish:

- `pending_approval`: show its exact approval link and wait for provider approval.
- `approved` with no successful transaction: payment may still be outstanding.
- A successful `payment_history` or `payment_status_details`: money already moved;
  reconcile the account and merchant subscription before another charge.

An old request's description is not evidence of which account received a plan.
A website error is not proof that no charge occurred. Re-read Link and billing
state after ambiguous submission; do not repeatedly click Subscribe.

## 3. Use the requested Link payment type

When the user asks to type a Link temporary card, create or reuse an approved
request with `credential_type: card`. A `link_pay_token` request cannot return a
virtual card number, and selecting a saved Link wallet card is not temporary-card
entry. Do not switch funding cards merely because the wrong checkout selection
was declined. Keep the user's chosen funding method.

Example for a verified $217.75 total (replace with the actual checkout total):

```bash
link-cli spend-request create --credential-type card \
  --merchant-name Anthropic --merchant-url https://claude.ai \
  --amount 21775 --currency usd \
  --context 'Initial Claude Max 20x monthly payment for EMAIL, connected to Agent LB. Total includes the verified checkout tax. Use a Link temporary virtual card for this payment; renewal requires a permanent payment method.' \
  --line-item 'name:Claude Max 20x monthly,unit_amount:20000,quantity:1' \
  --total 'type:total,display_text:Total due today,amount:21775' \
  --idempotency-key UNIQUE_PURCHASE_KEY --format json
```

Reuse the idempotency key only for retries of that same logical creation. Pass
`--payment-method-id` only when the funding method is known. Link authorization
is separate from permission to operate the browser: show the returned approval
URL directly, poll the same request, and resume as soon as its status is approved.
Do not file a second approval request through another service for the same payment.

Retrieve the approved card using `--include card --output-file SECURE_PATH` under
`umask 077`. Read the 0600 file only inside browser automation; never print the
CLI credential response, card number, CVC or payment field values. Fill card number,
expiry and CVC in the embedded payment frame. Do not print a frame's text after
filling credentials or capture the populated card fields. Delete the credential
file in `finally`, including on failures.

Only use the merchant-bound Link token flow when that is the chosen payment
method. Do not create both request types for the same purchase as a default.

## 4. Submit and handle phone verification

For an authorized purchase, submit once after checking email, plan and total.
If the user only requested filling the card, leave submission to them. A phone
prompt can occur before the charge. Report whether it asks for a number or an SMS
code and identify only the masked destination. Never claim paid while the request
has no successful payment and the website still blocks completion.

After the human finishes verification, re-read billing and Link payment history.
If a decline appears, inspect the selected payment method first: the saved wallet
card may have been selected instead of the filled temporary card. Do not infer the
funding card itself is unusable from that mismatch.

## 5. Connect OAuth immediately

```bash
agent-lb reauth EMAIL --provider anthropic --timeout 180 --json
```

Run as a yielding process and inspect the new authorization tab in the same
registered profile. Verify its full `Logged in as` email, then click Authorize
when account connection is authorized. The CLI collects the callback via CDP and
returns the imported account status. Never ask the human to copy an OAuth code
when the CLI can complete it. Do not print callback URLs or tokens.

A Max subscription does not automatically enroll the account in Agent LB.
`No Anthropic accounts are currently eligible` can persist until this OAuth step
finishes; buying the plan alone does not fix routing.

## 6. Prove readiness and record renewal

Read `GET /api/accounts`, select the exact provider and email, and use its
`accountId` for `POST /api/accounts/{accountId}/probe`. Require HTTP 200 from the
upstream probe and active status. If the user needs Opus, also probe that model
using the API's `model` field, then inspect `agent-lb status --provider anthropic
--model MODEL --thinking --json`. A stored token or `active` row alone is not proof
of a working subscription or model capacity.

Record the verified billing facts:

```bash
agent-lb subscriptions set EMAIL --plan 'Claude Max 20x' \
  --renews-on YYYY-MM-DD --payment single-use --amount TOTAL_USD \
  --note 'Initial payment via Link temporary card; needs a real card before renewal. OAuth connected; upstream probe HTTP 200.'
```

Use actual dates and amounts. Do not mark a saved permanent wallet card as a
single-use virtual card. Keep secrets out of optional local operator history.
Report the account, plan, live probe result and any remaining renewal action.
Never leave a successfully purchased account waiting for a later enrollment task.
