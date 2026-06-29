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

## Local Registry

Primary local file:

```
.agent-lb/account-profiles.json
```

This file is gitignored. It may contain account emails, Chrome profile paths,
billing URLs, manual subscription notes, and human-only action history. Do not
commit it. If it is missing, create it from:

```
.agents/skills/agent-lb-account-operator/account-profiles.example.json
```

Use the registry only as local operator memory. The agent-lb app database remains
the dashboard source of truth for account status, routing pause/reactivate state,
and subscription ledger fields.

## Operating Model

1. Read `.agent-lb/account-profiles.json` before account-management work.
2. Match the account by `accountId`, email, alias, or provider.
3. If a matching profile exists, open that browser profile rather than sharing a
   generic Chrome session.
4. If no profile exists, add an entry with `profileHandle`, `provider`,
   `accountId` or email if known, and a dedicated `userDataDir`.
5. If the user asks to add, open, verify, pause, or remove an account without
   naming the provider, ask whether it is OpenAI/ChatGPT or Anthropic/Claude
   before touching browser state or API rows.
6. Record manual billing actions in `subscriptionHistory` with exact dates.
7. Update agent-lb's subscription ledger after each human/browser billing action.
8. For quota reset, stuck account, rate-limited account, subscription status, or
   routing imbalance reports, inspect the matching dashboard/API account row and
   local profile notes before recommending billing, routing, or browser actions.

## Browser Profiles

Prefer dedicated Chrome user-data directories under:

```
.agent-lb/chrome-profiles/<profileHandle>
```

Open a profile on macOS with:

```bash
open -na "Google Chrome" --args --user-data-dir="$PWD/.agent-lb/chrome-profiles/<profileHandle>" "https://chatgpt.com"
```

Use the Chrome plugin only after the requested profile/browser is open and the
user asks for browser control. Keep one provider account per profile so cookies,
billing pages, and login state do not bleed across accounts.

## Subscription Ledger Mapping

Use these statuses in the agent-lb dashboard/API ledger:

- `active`: subscription is expected to renew on `nextChargeAt`.
- `cancel_pending`: vendor cancellation completed, access remains until
  `currentPeriodEndAt`.
- `pause_pending`: operator intends to pause/cancel before `nextChargeAt`, but
  vendor action is not yet complete.
- `paused`: vendor subscription or local routing should be treated as paused;
  check notes for which one.
- `canceled`: vendor access period ended or subscription is fully canceled.

For screenshots like "will remain active until ... Jun 22, 2026", record:

```json
{
  "status": "cancel_pending",
  "currentPeriodEndAt": "2026-06-22T04:00:00.000Z",
  "nextChargeAt": null
}
```

## Safety

- Do not store passwords, recovery codes, access tokens, refresh tokens, card
  numbers, or secrets in the registry.
- Do not submit purchase, cancellation, reactivation, payment-method, or plan
  upgrade actions without explicit user confirmation in the current turn.
- Distinguish vendor subscription state from agent-lb routing state. Pausing an
  account in agent-lb only removes it from routing; it does not cancel vendor
  billing.
