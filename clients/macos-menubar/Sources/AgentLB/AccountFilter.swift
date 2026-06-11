import Foundation

/// Pure account filter/sort logic for the Accounts section (§8.2 — dashboard
/// parity). Kept free of SwiftUI so it is unit-testable with fixture data.
struct AccountFilter: Equatable, Sendable {
  enum Provider: String, CaseIterable, Sendable {
    case all, anthropic, openai
  }

  enum Status: String, CaseIterable, Sendable {
    case all, active, rateLimited, paused, inactive
  }

  enum Sort: String, CaseIterable, Sendable {
    case resetSoonest, resetLatest, nameAsc, nameDesc
  }

  var provider: Provider = .all
  var status: Status = .all
  var query: String = ""
  var sort: Sort = .resetSoonest

  /// True when any narrowing filter is applied (sort alone never counts).
  var hasActiveFilters: Bool {
    provider != .all || status != .all || !trimmedQuery.isEmpty
  }

  private var trimmedQuery: String {
    query.trimmingCharacters(in: .whitespacesAndNewlines)
  }

  // MARK: - Classification

  /// Effective status; mirrors AccountRow presentation priority:
  /// rate-limited (synthetic, `rateLimitResetAt` in the future) beats paused
  /// beats deactivated.
  static func classify(_ account: Account, now: Date) -> Status {
    if let reset = account.rateLimitResetAt, reset > now { return .rateLimited }
    if account.status == "paused" { return .paused }
    if account.status == "deactivated" || account.deactivationReason != nil { return .inactive }
    return .active
  }

  /// Earliest *future* reset across the primary/secondary windows — the
  /// reset-sort key. Nil when neither window resets in the future.
  static func nextReset(of account: Account, now: Date) -> Date? {
    [account.resetAtPrimary, account.resetAtSecondary]
      .compactMap { $0 }
      .filter { $0 > now }
      .min()
  }

  // MARK: - Apply

  func apply(to accounts: [Account], now: Date) -> [Account] {
    sorted(
      accounts.filter {
        matchesProvider($0) && matchesStatus($0, now: now) && matchesQuery($0)
      },
      now: now
    )
  }

  /// Per-provider counts with the *other* filters (status + query) applied —
  /// the provider chips show what each selection would yield (§8.2).
  func providerCounts(in accounts: [Account], now: Date) -> [Provider: Int] {
    var counts: [Provider: Int] = [.all: 0, .anthropic: 0, .openai: 0]
    for account in accounts where matchesStatus(account, now: now) && matchesQuery(account) {
      counts[.all, default: 0] += 1
      if let provider = Provider(rawValue: account.provider.lowercased()) {
        counts[provider, default: 0] += 1
      }
    }
    return counts
  }

  // MARK: - Predicates

  private func matchesProvider(_ account: Account) -> Bool {
    provider == .all || account.provider.lowercased() == provider.rawValue
  }

  private func matchesStatus(_ account: Account, now: Date) -> Bool {
    status == .all || Self.classify(account, now: now) == status
  }

  private func matchesQuery(_ account: Account) -> Bool {
    let q = trimmedQuery
    guard !q.isEmpty else { return true }
    let haystacks = [account.displayName, account.email, account.alias]
    return haystacks.contains { $0?.localizedCaseInsensitiveContains(q) == true }
  }

  // MARK: - Sort

  private func sorted(_ accounts: [Account], now: Date) -> [Account] {
    switch sort {
    case .resetSoonest:
      return accounts.sorted { byReset($0, $1, now: now, ascending: true) }
    case .resetLatest:
      return accounts.sorted { byReset($0, $1, now: now, ascending: false) }
    case .nameAsc:
      return accounts.sorted { byName($0, $1, ascending: true) }
    case .nameDesc:
      return accounts.sorted { byName($0, $1, ascending: false) }
    }
  }

  /// Nil resets always sort last regardless of direction; ties fall back to
  /// name then id so the order is deterministic for equal keys.
  private func byReset(_ lhs: Account, _ rhs: Account, now: Date, ascending: Bool) -> Bool {
    switch (Self.nextReset(of: lhs, now: now), Self.nextReset(of: rhs, now: now)) {
    case (nil, nil): return byName(lhs, rhs, ascending: true)
    case (nil, _): return false
    case (_, nil): return true
    case (let l?, let r?):
      if l != r { return ascending ? l < r : l > r }
      return byName(lhs, rhs, ascending: true)
    }
  }

  // Plain lowercased comparison (not localized collation) so ordering is
  // deterministic across locales — matches the dashboard's JS sort.
  private func byName(_ lhs: Account, _ rhs: Account, ascending: Bool) -> Bool {
    let l = lhs.displayName.lowercased()
    let r = rhs.displayName.lowercased()
    if l != r { return ascending ? l < r : l > r }
    return lhs.accountId < rhs.accountId
  }
}
