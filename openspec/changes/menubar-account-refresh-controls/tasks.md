# Tasks

- [x] `APIClient.checkSubscription(_:)` / `probeAccount(_:)` on a slower probe
      session; response models in `APIModels.swift`.
- [x] Pure `AccountRefreshAction.action(for:)` endpoint-choice function
      (canceled → subscription check, paused/disconnected → none, else probe).
- [x] `AppState.refresh(accountId:via:)` with the pause/reactivate contract
      (false on failure, accounts refreshed on success).
- [x] `AccountRow` hover-revealed refresh control: fixed 16 pt slot, spinner in
      flight, checkmark/exclamation hint for 2 s, always visible on
      unsubscribed rows.
- [x] Tests: endpoint choice (canceled/paused/disconnected/rate-limited/active)
      and probe/subscription-check response decoding (`swift test`, 134
      passing).
- [x] OpenSpec delta (`macos-menubar-client`).
- [x] Rebuild and reinstall the local menubar bundle (`make install`).
