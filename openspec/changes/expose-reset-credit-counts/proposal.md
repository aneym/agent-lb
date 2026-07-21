## Why

Banked OpenAI rate-limit reset credits are invisible outside the per-account listing endpoint, which calls upstream live on every request. The operator cannot see from the menu bar how many rescue resets each Codex account still has banked — the number that determines whether the auto-redeem scheduler can save an exhausted pool.

## What Changes

- Maintain a server-side in-memory cache of each OpenAI account's available reset-credit count, updated opportunistically from every existing upstream credit listing (hourly expiry sweep, exhaustion-ranking sweep, manual listing endpoint) and refreshed best-effort after every successful redemption. No new periodic upstream traffic and no schema migration.
- Expose the cached count as an optional `resetCreditsAvailable` field on each account in `GET /api/accounts` (`null` until first observation; OpenAI accounts only).
- Show the count as a small per-account chip on OpenAI rows in the macOS menu bar popover.

## Capabilities

### Modified Capabilities

- `usage-refresh-policy`: Add cached reset-credit count maintenance rules.
- `frontend-architecture`: Add the `resetCreditsAvailable` accounts-payload field.
- `macos-menubar`: Add the per-account banked-resets chip.

## Impact

`app/modules/accounts/` (new cache module, mapper field, scheduler/service touchpoints), `clients/macos-menubar/` (model field + row chip). The menubar's 5-second poll causes zero additional upstream OpenAI traffic. Older servers omit the field; the menubar treats it as unknown and shows nothing.
