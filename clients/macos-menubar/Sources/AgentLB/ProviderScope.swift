import Foundation

/// §9.2 top-level provider scope (dashboard parity). Pure, testable logic:
/// segment labels/counts, account scoping, and the scoped pool-window math
/// (dashboard `summarizeFilteredWindow` semantics).
enum ProviderScope: String, CaseIterable, Sendable {
  case all
  case openai
  case anthropic

  /// Dashboard `providerLabel`: openai → "Codex", anthropic → "Claude".
  var label: String {
    switch self {
    case .all: "All"
    case .openai: "Codex"
    case .anthropic: "Claude"
    }
  }

  func includes(_ account: Account) -> Bool {
    self == .all || account.provider.lowercased() == rawValue
  }

  func filter(_ accounts: [Account]) -> [Account] {
    accounts.filter { includes($0) }
  }

  /// Live segment counts over the full (unfiltered) accounts list.
  static func counts(in accounts: [Account]) -> [ProviderScope: Int] {
    var counts: [ProviderScope: Int] = [.all: 0, .openai: 0, .anthropic: 0]
    for account in accounts where account.isHeadlineCountable {
      counts[.all, default: 0] += 1
      if let scope = ProviderScope(rawValue: account.provider.lowercased()) {
        counts[scope, default: 0] += 1
      }
    }
    return counts
  }

  // MARK: - Scoped pool windows (§9.2 / §10)

  enum Window: Sendable {
    case primary, secondary
  }

  /// A scoped pool window plus the §10 "next reset" recovery: the summed
  /// percentage never resets at one moment (per-account clocks stagger), so
  /// the card reports the credits the pool gets back at the NEXT reset.
  struct ScopedPoolWindow: Equatable, Sendable {
    let usage: UsageWindow
    /// Σ(capacity − remaining) over the accounts whose window resets in the
    /// earliest future 1-minute bucket; nil when no bucket account exposes
    /// credit fields (or there is no future reset at all).
    let recoveredCredits: Double?
  }

  /// One account's contribution to the per-window reset schedule (§10
  /// tooltip), soonest first.
  struct AccountReset: Equatable, Sendable {
    let displayName: String
    let resetAt: Date
    /// capacity − remaining for this account's window; nil when unknown.
    let recoveredCredits: Double?
  }

  /// Recomputes a pool window from per-account credits:
  ///   percent   = Σ remainingCredits / Σ capacityCredits × 100
  ///   credits   = the same sums
  ///   resetAt   = earliest FUTURE reset among the accounts
  ///   recovered = Σ(capacity − remaining) over the accounts resetting
  ///               within 1 minute of that earliest reset (§10)
  /// Accounts missing either credit field do not contribute to the sums.
  /// No contributing accounts (or zero capacity) → nil fields, which the
  /// pool cards render as "—".
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
      if let reset = windowReset(of: account, window: window), reset > now,
         earliestReset.map({ reset < $0 }) ?? true {
        earliestReset = reset
      }
    }

    // §10: the recovery at the next reset — only accounts in the earliest
    // future 1-minute bucket count; staggered later resets do not.
    var recovered: Double?
    if let earliest = earliestReset {
      let bucketEnd = earliest.addingTimeInterval(60)
      for account in routableAccounts {
        guard let reset = windowReset(of: account, window: window),
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

  /// Per-account reset schedule for a window, soonest first (§10 tooltip).
  /// Only future resets appear; ties order by display name for determinism.
  static func resetSchedule(
    _ accounts: [Account], window: Window, now: Date
  ) -> [AccountReset] {
    accounts
      .filter { $0.isRoutable }
      .compactMap { account -> AccountReset? in
        guard let reset = windowReset(of: account, window: window), reset > now else {
          return nil
        }
        let recovery = windowCredits(of: account, window: window)
          .map { $0.capacity - $0.remaining }
        return AccountReset(
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

  // MARK: - Per-account window accessors

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
