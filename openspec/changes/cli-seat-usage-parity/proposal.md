## Why

Alex, 2026-10-16: "can we not see muse usage via api? ... it should be designed same as claude or codex", "update dashboard end to end to support muse as well", and "we should be using muse and grok more often, and the menubar is ugly for them ... we really need parity and to handle this properly."

Claude and Codex have a quota pipeline: a vendor usage client, a refresh scheduler, stored windows, and account rows that show state, a limit bar, the reset and today's usage. The CLI-seat vendors (Cursor, which carries Grok, plus Devin and Muse) only have an observation pipeline: a seat state file with readiness and run counts. Every surface then falls back to placeholders. The menubar shows empty 5-HOUR and WEEKLY rings, "0 accts", "$0.0000 · 7d 0 req · all providers" and plain-text account sentences. The web dashboard shows the Anthropic glyph and "No reading" for Muse and labels every non-Devin seat "Cursor".

The vendors expose usage. Cursor's DashboardService returns vendor percentages and the billing cycle; this was live-verified on 2026-10-16 at 9:42 AM ET. Devin's SeatManagementService returns daily and weekly quota. Muse's host reports a session window and a weekly window (`SubscriptionUsage`), but reading it takes Alex's login, and agents may not use that login without a provisioned key.

## What Changes

- `seat quota` collects per-account vendor quota for Cursor, Devin and Muse and stores it in seat state with its source and observation time. A backend scheduler refreshes it every 5 minutes, and every seat run refreshes the account it used.
- `/api/pools/seat-accounts` gains a `quota` block with vendor windows, a `status` that uses the proxy account vocabulary, `today` usage with honest unknowns, the masked-by-client `email`, and the plan label.
- `/api/pools` CLI-seat pools gain display `windows` built from measured account quota. Routing fields keep their current meaning.
- `/api/usage/summary?provider=` gains `seatActivity`, so seat runs, tokens and estimated value are counted per provider and unknown values never read as zero.
- The web dashboard and the macOS menubar render CLI-seat accounts with the same row components as Claude and Codex. Each vendor has its own mark, label and scope tab (Muse added), uses its real window kinds (monthly for legacy Cursor, daily and weekly for Devin, session and weekly for Muse), and shows provider-scoped activity. Grok stays a model inside Cursor and is named in the Cursor models window and in the activity.
- `route pools` stops inventing Muse headroom and zero tokens.

## Capabilities

### New Capabilities

- `cli-seat-usage`: vendor quota collection, the shared seat usage contract and provider-scoped seat activity.

### Modified Capabilities

- `macos-menubar`: vendor tabs, seat account rows and scoped metrics.
- `frontend-architecture`: seat provider identity, seat account rows, pool windows and usage scope on the dashboard.

## Impact

Backend: `clients/seat`, `clients/route`, `app/modules/pools/` (`cli_seats.py`, `schemas.py`, `service.py`, `api.py`, new `seat_quota_scheduler.py`), `app/modules/usage/` (`service.py`, `schemas.py`), `app/main.py` (scheduler start and stop), and tests. Web: `frontend/src/lb/**`, `frontend/src/features/dashboard/pool-rows.ts`, a Muse mark under `frontend/public/brand/providers/`, and tests. Menubar: `clients/macos-menubar/**`. The contract is in `design.md`, and an example payload is in `contract/seat-usage.example.json`.

Money path: `clients/seat` handles seat credentials, and `clients/route` is seat routing. Readiness, cooldowns, `headroomPercent` and seat selection are unchanged. The new fields are for display and advisory use only.
