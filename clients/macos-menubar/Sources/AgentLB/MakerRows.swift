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
      let label = pool.id == "cursor-models" ? "Cursor models" : "Other models"
      let spent = String(format: "%.2f", locale: Locale(identifier: "en_US_POSIX"), pool.spentUsd ?? 0)
      guard let budget = pool.budgetUsd else {
        return "\(label): $\(spent) spent, no published size"
      }
      let budgetText = String(
        format: budget.rounded() == budget ? "%.0f" : "%.2f",
        locale: Locale(identifier: "en_US_POSIX"), budget
      )
      let remaining = pool.monthlyRemainingPercent.map {
        String(format: "%.0f", locale: Locale(identifier: "en_US_POSIX"), $0.rounded())
      } ?? "—"
      return "\(label): $\(spent) of $\(budgetText), \(remaining)% left"
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
        } else {
          state = "ready"
        }
        let tier = scope == .cursor ? "\(seat.tier ?? "tier unknown"), " : ""
        return "\(seat.id): \(tier)\(state)"
      }
    return poolLines + accountLines
  }
}
