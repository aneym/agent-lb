import Foundation

enum MakerRows {
  static func lines(for scope: ProviderScope, seats: [SeatAccount], pools: [PoolEntry]) -> [String] {
    guard scope == .cursor || scope == .devin else { return [] }
    let poolLines = pools.filter {
      $0.provider == scope.rawValue && (scope == .devin || $0.kind == "cli_seat_budget")
    }.map { pool in
      if scope == .devin {
        return "Devin: \(pool.eligibleAccounts) of \(pool.accounts) ready, \(pool.observedRuns ?? 0) runs in 24 h"
      }
      let label = cursorLabel(for: pool.id)
      let usage: String
      if let percent = pool.percentUsed {
        let percentText = String(format: "%.0f", locale: Locale(identifier: "en_US_POSIX"), percent.rounded())
        switch pool.percentSource {
        case "vendor":
          usage = "\(percentText)% used"
        case "estimate":
          usage = "~\(percentText)% used (estimate)"
        default:
          usage = "usage unknown"
        }
      } else {
        usage = "usage unknown"
      }
      let reset = pool.cycleResetAt.map { ", resets \(cursorDate($0))" } ?? ""
      return "\(label): \(usage)\(reset)"
    }
    let accountLines = seats.filter { $0.vendor.lowercased() == scope.rawValue }
      .sorted { $0.id < $1.id }
      .map { seat in
        let state: String
        if !seat.enabled {
          state = "disabled"
        } else if seat.authOk == false {
          state = "auth failed"
        } else if seat.cooldownUntil != nil {
          state = "cooling down"
        } else if scope == .cursor {
          let liveCooldowns = (seat.cooldowns ?? [:]).filter { $0.value > Date() }
          let poolOuts = liveCooldowns
            .sorted { $0.key < $1.key }
            .map { "\(cursorLabel(for: $0.key)) out until \(cursorDate($0.value))" }
          let otherPoolsReady = pools.contains {
            $0.provider == "cursor" && $0.kind == "cli_seat_budget" && liveCooldowns[$0.id] == nil
          }
          state = poolOuts.isEmpty ? "ready" : poolOuts.joined(separator: "; ")
            + (otherPoolsReady ? "; other pools ready" : "")
        } else {
          state = "ready"
        }
        let tier = scope == .cursor ? "\(seat.tier ?? "plan unknown"), " : ""
        return "\(seat.id): \(tier)\(state)"
      }
    return poolLines + accountLines
  }

  private static func cursorLabel(for poolID: String) -> String {
    poolID == "cursor-models" ? "Cursor models" : "Other models"
  }

  private static func cursorDate(_ date: Date) -> String {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = TimeZone(identifier: "America/New_York")
    formatter.dateFormat = "MMM d"
    return formatter.string(from: date)
  }
}
