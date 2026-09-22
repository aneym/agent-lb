import Foundation

/// Pure presentation rules for quota reset state. A reset timestamp marks a
/// window boundary, not a promise that more allowance will become available.
enum ResetDisplay {
  static func isKnownFull(_ window: UsageWindow) -> Bool {
    if let remaining = window.remainingCredits, let capacity = window.capacityCredits {
      return capacity > 0 && remaining >= capacity
    }
    return window.remainingPercent.map { $0 >= 100 } ?? false
  }

  static func isKnownFull(_ account: Account, window: ProviderScope.Window) -> Bool {
    let values: (remaining: Double?, capacity: Double?, percent: Double?) = switch window {
    case .primary: (
      account.remainingCreditsPrimary,
      account.capacityCreditsPrimary,
      account.usage.primaryRemainingPercent
    )
    case .secondary: (
      account.remainingCreditsSecondary,
      account.capacityCreditsSecondary,
      account.usage.secondaryRemainingPercent
    )
    }
    if let remaining = values.remaining, let capacity = values.capacity {
      return capacity > 0 && remaining >= capacity
    }
    return values.percent.map { $0 >= 100 } ?? false
  }

  /// A scoped card is full only when every routable account has known-full
  /// telemetry for that window. Missing telemetry deliberately remains unknown.
  static func allKnownFull(_ accounts: [Account], window: ProviderScope.Window) -> Bool {
    let routable = accounts.filter { $0.isRoutable }
    return !routable.isEmpty && routable.allSatisfy { isKnownFull($0, window: window) }
  }

  static func resetText(resetAt: Date?, knownFull: Bool, recoveredCredits: Double?, now: Date) -> String {
    if knownFull { return "Full · no reset needed" }
    guard let resetAt else { return "next reset —" }
    var text = "next reset in \(Format.countdownCompact(to: resetAt, relativeTo: now))"
    if let recoveredCredits, recoveredCredits > 0 {
      text += " · \(recoveryCredits(recoveredCredits))"
    }
    return text
  }

  static func recoveryCredits(_ value: Double) -> String {
    value > 0 && value < 1 ? "+<1 cr" : "+\(Format.compactCredits(value)) cr"
  }
}
