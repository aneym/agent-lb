# Add account subscription ledger

Operators need a lightweight way to see each imported account's next vendor charge date from the codex-lb dashboard. The upstream account APIs available to codex-lb expose plan, auth, and usage metadata, but not a reliable billing renewal contract. The dashboard should therefore store a local operator-maintained subscription ledger per account.

## Scope

- Persist local subscription metadata on account rows: status, next charge date, active-until date, amount/currency, last verified time, and notes.
- Expose those fields in `GET /api/accounts`.
- Add an account detail editor for maintaining the ledger.
- Surface the next charge date in the account list so the upcoming account is easy to spot.
- Add a project skill and gitignored local profile registry pattern for browser-profile based vendor account operations.
- Keep existing load-balancer pause/resume semantics unchanged.

## Non-goals

- Automatically cancel, pause, or modify vendor-side subscriptions.
- Scrape billing pages or store payment credentials.
- Treat locally entered billing metadata as upstream-verified truth.
