import Foundation




enum ProviderScope: String, CaseIterable, Sendable {
  case all
  case openai
  case anthropic
  case cursor
  case devin
  case muse
  case other


  var label: String {
    switch self {
    case .all: "All"
    case .openai: "Codex"
    case .anthropic: "Claude"
    case .cursor: "X Ultra"
    case .devin: "Devin"
    case .muse: "Muse"
    case .other: "Other"
    }
  }

  static let seatVendors: [ProviderScope] = [.cursor, .devin, .muse]

  var isSeatVendor: Bool {
    Self.seatVendors.contains(self)
  }

  func includes(_ seat: SeatAccount) -> Bool {
    if self == .all { return true }
    return seat.vendor.lowercased() == rawValue && isSeatVendor
  }

  func filter(_ seats: [SeatAccount]) -> [SeatAccount] {
    seats.filter { includes($0) }
  }

  func includes(_ account: Account) -> Bool {
    if self == .all { return true }
    let provider = account.provider.lowercased()
    if self == .other {
      return !["openai", "anthropic", "cursor", "devin", "muse"].contains(provider)
    }
    return provider == rawValue
  }



  var providerParam: String? {
    self == .all ? nil : rawValue
  }

  func filter(_ accounts: [Account]) -> [Account] {
    accounts.filter { includes($0) }
  }


  static func counts(in accounts: [Account], seats: [SeatAccount] = []) -> [ProviderScope: Int] {
    var counts = Dictionary(uniqueKeysWithValues: allCases.map { ($0, 0) })
    for account in accounts where account.isHeadlineCountable {
      counts[.all, default: 0] += 1
      let providerScope = ProviderScope(rawValue: account.provider.lowercased()) ?? .other
      let scope: ProviderScope = providerScope == .all ? .other : providerScope
      counts[scope, default: 0] += 1
    }
    for seat in seats {
      counts[.all, default: 0] += 1
      if let scope = seat.scope {
        counts[scope, default: 0] += 1
      }
    }
    return counts
  }



  enum Window: Sendable {
    case primary, secondary
  }




  struct ScopedPoolWindow: Equatable, Sendable {
    let usage: UsageWindow



    let recoveredCredits: Double?
  }



  struct AccountReset: Equatable, Sendable {

    let accountId: String
    let displayName: String
    let resetAt: Date

    let recoveredCredits: Double?
  }










  static func summarizeWindow(
    _ accounts: [Account], window: Window, now: Date
  ) -> ScopedPoolWindow {
    let routableAccounts = accounts.filter { $0.isRoutable }
    var remaining = 0.0
    var capacity = 0.0
    var contributed = false
    var earliestReset: Date?

    for account in routableAccounts {
      if let credits = windowCredits(of: account, window: window) {
        remaining += credits.remaining
        capacity += credits.capacity
        contributed = true
      }
      if !ResetDisplay.isKnownFull(account, window: window),
         let reset = windowReset(of: account, window: window), reset > now,
         earliestReset.map({ reset < $0 }) ?? true {
        earliestReset = reset
      }
    }



    var recovered: Double?
    if let earliest = earliestReset {
      let bucketEnd = earliest.addingTimeInterval(60)
      for account in routableAccounts {
        guard !ResetDisplay.isKnownFull(account, window: window),
              let reset = windowReset(of: account, window: window),
              reset > now, reset < bucketEnd,
              let credits = windowCredits(of: account, window: window)
        else { continue }
        recovered = (recovered ?? 0) + (credits.capacity - credits.remaining)
      }
    }

    let percent: Double? = (contributed && capacity > 0) ? remaining / capacity * 100 : nil
    return ScopedPoolWindow(
      usage: UsageWindow(
        remainingPercent: percent,
        capacityCredits: contributed ? capacity : nil,
        remainingCredits: contributed ? remaining : nil,
        resetAt: earliestReset,
        windowMinutes: nil
      ),
      recoveredCredits: recovered
    )
  }



  static func resetSchedule(
    _ accounts: [Account], window: Window, now: Date
  ) -> [AccountReset] {
    accounts
      .filter { $0.isRoutable }
      .compactMap { account -> AccountReset? in
        guard !ResetDisplay.isKnownFull(account, window: window),
              let reset = windowReset(of: account, window: window), reset > now else {
          return nil
        }
        let recovery = windowCredits(of: account, window: window)
          .map { $0.capacity - $0.remaining }
        return AccountReset(
          accountId: account.accountId,
          displayName: account.displayName,
          resetAt: reset,
          recoveredCredits: recovery
        )
      }
      .sorted {
        if $0.resetAt != $1.resetAt { return $0.resetAt < $1.resetAt }
        return $0.displayName.lowercased() < $1.displayName.lowercased()
      }
  }



  private static func windowCredits(
    of account: Account, window: Window
  ) -> (remaining: Double, capacity: Double)? {
    let pair: (Double?, Double?) = switch window {
    case .primary: (account.remainingCreditsPrimary, account.capacityCreditsPrimary)
    case .secondary: (account.remainingCreditsSecondary, account.capacityCreditsSecondary)
    }
    guard let remaining = pair.0, let capacity = pair.1 else { return nil }
    return (remaining, capacity)
  }

  private static func windowReset(of account: Account, window: Window) -> Date? {
    window == .primary ? account.resetAtPrimary : account.resetAtSecondary
  }
}
