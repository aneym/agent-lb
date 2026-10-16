## 1. Backend (python + tests)

- [ ] 1.1 `clients/seat`: add `seat quota [--vendor V] [--account A] [--json]` with Cursor, Devin and Muse readers as listed in design.md, store `accounts.<id>.quota` under the state lock, and refresh the used account after `seat run`. In the Muse run parser, record `SubscriptionUsage` frames. Store `ok` and `wall_s` on runs (already present) and keep null tokens null.
- [ ] 1.2 `app/modules/pools/cli_seats.py`: add `SeatQuota`, `SeatQuotaWindow`, `SeatToday`, plus `email`, `planLabel`, `status`, `statusResetAt` and `quotaRefreshedAt`. Use the Cursor estimate fallback for each account and a pool `windows` and `quotaSource` builder. Leave routing fields unchanged.
- [ ] 1.3 `app/modules/pools/schemas.py`: add `PoolWindow`, `windows` and `quotaSource`, and widen `percentSource` to `vendor_observed`. `service.py` and `api.py` pass them through.
- [ ] 1.4 Add `app/modules/pools/seat_quota_scheduler.py`, started and stopped in `app/main.py` next to the usage scheduler.
- [ ] 1.5 `app/modules/usage/schemas.py` and `service.py`: add `provider` and `seatActivity`.
- [ ] 1.6 `clients/route`: `route pools` prints the server values for Muse.
- [ ] 1.7 Tests: `tests/unit/test_seat_quota.py` (vendor response fixtures for the three vendors, no network, plus the error-keeps-last-reading case), `tests/unit/test_cli_seat_quota_contract.py` (serializer key set equals `contract/seat-usage.example.json`), and extended `tests/integration/test_pools_api.py` and `tests/unit/test_cli_seat_muse_pool.py`.

## 2. Web (frontend/src + tests)

- [ ] 2.1 `frontend/src/lb/api/index.ts`: seat, pool and summary schemas per the contract. `authOk` is nullable.
- [ ] 2.2 `frontend/src/lb/kit/provider-mark-helpers.ts` and `provider-mark.tsx`: add `muse` and a neutral fallback. Add `frontend/public/brand/providers/muse.svg`.
- [ ] 2.3 `frontend/src/lb/pages/providers/providers-page.tsx`: seat rows use `AccountRow` meters from `quota.windows`, label by vendor and fill the today column. Pool rows use `windows`. Remove the toast.
- [ ] 2.4 `frontend/src/lb/pages/usage/usage-page.tsx`, `frontend/src/lb/pages/routing/data.ts`, `pipeline-tab.tsx` and `frontend/src/features/dashboard/pool-rows.ts`: add the seat vendors, `seatActivity` and the monthly or unavailable states.
- [ ] 2.5 Tests: `frontend/src/lb/pages/providers/providers-page.test.tsx` over the contract fixture, `frontend/src/lb/kit/provider-mark-helpers.test.ts`, and `frontend/src/lb/api/seat-schema.test.ts`.

## 3. Menubar (clients/macos-menubar)

- [ ] 3.1 `APIModels.swift`: decode the full seat, pool `windows` and summary contract.
- [ ] 3.2 `ProviderScope.swift`: add `.muse` and count seats per vendor.
- [ ] 3.3 `Views/RootView.swift` and `Views/PoolSection.swift`: vendor tabs render pool `windows` cards, seat counts and a scoped strip with `seatActivity`. Remove the literal "all providers" from scoped tabs.
- [ ] 3.4 `Views/AccountsSection.swift`: move the AccountRow ring, chips and window grid into shared views, render seat rows with them, and include seats in search, sort and privacy (`PrivacyMask.swift`, `AccountFilter.swift`). Delete `MakerRows.swift`.
- [ ] 3.5 `PoolMenu.swift`: add the Muse tile and derive tile order from the pools.
- [ ] 3.6 `Format.swift`: dollars to two decimals, and ET reset dates.
- [ ] 3.7 Tests: `Tests/AgentLBTests/SeatParityTests.swift` renders the Cursor, Devin and Muse tabs from `Sources/AgentLB/Fixtures/seat-usage.example.json` with ImageRenderer, light and dark, and writes `Tests/Snapshots/seat-*.png`. Update `ProviderScopeTests` and `MakerScopeTests`.

## 4. End to end

- [ ] 4.1 After `lb-restart`, live `/api/pools/seat-accounts` shows a Cursor `vendor` reading, a Devin `vendor` reading (or a named reason), and Muse `not_reported` or `vendor_observed`.
- [ ] 4.2 Web screenshot of Providers, filtered to Cursor, Devin and Muse.
- [ ] 4.3 Menubar screenshots of the Cursor, Devin and Muse tabs.
- [ ] 4.4 `desktop-guard-focus` count before and after the quota refresh is unchanged.
