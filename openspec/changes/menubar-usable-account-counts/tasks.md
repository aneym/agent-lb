# Tasks

- [x] Add shared `Account` state helpers (`isSubscriptionCanceled`,
      `isDisconnected`, `isHeadlineCountable`, `isRoutable`).
- [x] Scope-bar and pool-card counts use `isHeadlineCountable`; pool window
      aggregation uses `isRoutable`.
- [x] Show subscription-canceled accounts as dimmed `unsubscribed` rows; classify
      `reauth_required` without a deactivation reason as needing re-auth.
- [x] Tests: count exclusions, pool-% exclusions (canceled + paused), canceled-row
      visibility, reauth_required classification (`swift test`, 120 passing).
- [x] OpenSpec delta (`macos-menubar-client`).
- [x] Rebuild and reinstall the local menubar bundle.
