import Foundation

enum AccountListItem: Identifiable, Equatable, Sendable {
  case proxy(Account)
  case seat(SeatAccount)

  var id: String {
    switch self {
    case .proxy(let account): "proxy:" + account.accountId
    case .seat(let seat): "seat:" + seat.id
    }
  }
}



struct AccountFilter: Equatable, Sendable {
  enum Provider: String, CaseIterable, Sendable {
    case all, anthropic, openai
  }

  enum Status: String, CaseIterable, Sendable {
    case all, active, rateLimited, paused, inactive, unsubscribed
  }

  enum Sort: String, CaseIterable, Sendable {
    case resetSoonest, resetLatest, nameAsc, nameDesc
    case remainingAsc, remainingDesc
  }

  var provider: Provider = .all
  var status: Status = .all
  var query: String = ""
  var sort: Sort = .resetSoonest


  var hasActiveFilters: Bool {
    provider != .all || status != .all || !trimmedQuery.isEmpty
  }

  private var trimmedQuery: String {
    query.trimmingCharacters(in: .whitespacesAndNewlines)
  }






  static func classify(_ account: Account, now: Date) -> Status {
    if account.isSubscriptionCanceled { return .unsubscribed }
    if account.status == "paused" { return .paused }
    if account.isDisconnected { return .inactive }
    if account.status == "rate_limited" || account.status == "quota_exceeded" {
      return .rateLimited
    }
    return .active
  }



  static func nextReset(of account: Account, now: Date) -> Date? {
    [account.resetAtPrimary, account.resetAtSecondary]
      .compactMap { $0 }
      .filter { $0 > now }
      .min()
  }



  static func classify(_ seat: SeatAccount) -> Status {
    switch seat.displayStatus {
    case "paused": return .paused
    case "reauth_required": return .inactive
    case "rate_limited": return .rateLimited
    default: return .active
    }
  }

  static func nextReset(of seat: SeatAccount, now: Date) -> Date? {
    ([seat.displayStatusResetAt] + seat.quotaWindows.map(\.resetsAt))
      .compactMap { $0 }
      .filter { $0 > now }
      .min()
  }

  func apply(to seats: [SeatAccount], now: Date) -> [SeatAccount] {
    apply(to: [], seats: seats, now: now).compactMap {
      if case .seat(let seat) = $0 { return seat }
      return nil
    }
  }

  func apply(to accounts: [Account], seats: [SeatAccount], now: Date) -> [AccountListItem] {
    let proxies = accounts.filter {
      matchesProvider($0) && matchesStatus($0, now: now) && matchesQuery($0)
    }.map { AccountListItem.proxy($0) }
    let seatItems = seats.filter {
      matchesProvider($0) && (status == .all || Self.classify($0) == status) && matchesQuery($0)
    }.map { AccountListItem.seat($0) }
    let keyed = (proxies + seatItems).map { ($0, sortKey($0, now: now)) }
    return keyed.sorted { ordered($0.1, $1.1) }.map(\.0)
  }

  private struct SortKey {
    let name: String
    let id: String
    let reset: Date?
    let remaining: Double?
  }

  private func sortKey(_ item: AccountListItem, now: Date) -> SortKey {
    switch item {
    case .proxy(let account):
      return SortKey(
        name: account.displayName.lowercased(),
        id: account.accountId,
        reset: Self.nextReset(of: account, now: now),
        remaining: remainingPercent(of: account)
      )
    case .seat(let seat):
      return SortKey(
        name: seat.id.lowercased(),
        id: seat.id,
        reset: Self.nextReset(of: seat, now: now),
        remaining: seat.primaryQuotaWindow?.remainingPercent
      )
    }
  }

  private func ordered(_ lhs: SortKey, _ rhs: SortKey) -> Bool {
    func byName(_ ascending: Bool) -> Bool {
      if lhs.name != rhs.name { return ascending ? lhs.name < rhs.name : lhs.name > rhs.name }
      return lhs.id < rhs.id
    }
    func byOptional<T: Comparable>(_ l: T?, _ r: T?, ascending: Bool) -> Bool {
      switch (l, r) {
      case (nil, nil): return byName(true)
      case (nil, _): return false
      case (_, nil): return true
      case (let l?, let r?):
        if l != r { return ascending ? l < r : l > r }
        return byName(true)
      }
    }
    switch sort {
    case .resetSoonest: return byOptional(lhs.reset, rhs.reset, ascending: true)
    case .resetLatest: return byOptional(lhs.reset, rhs.reset, ascending: false)
    case .nameAsc: return byName(true)
    case .nameDesc: return byName(false)
    case .remainingAsc: return byOptional(lhs.remaining, rhs.remaining, ascending: true)
    case .remainingDesc: return byOptional(lhs.remaining, rhs.remaining, ascending: false)
    }
  }

  private func matchesProvider(_ seat: SeatAccount) -> Bool {
    provider == .all || seat.vendor.lowercased() == provider.rawValue
  }

  private func matchesQuery(_ seat: SeatAccount) -> Bool {
    let q = trimmedQuery
    guard !q.isEmpty else { return true }
    let haystacks = [seat.id, seat.email, seat.planLabel ?? seat.tier, seat.vendorLabel]
    return haystacks.contains { $0?.localizedCaseInsensitiveContains(q) == true }
  }

  func apply(to accounts: [Account], now: Date) -> [Account] {
    sorted(
      accounts.filter {
        matchesProvider($0)
          && matchesStatus($0, now: now)
          && matchesQuery($0)
      },
      now: now
    )
  }



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
    case .remainingAsc:
      return accounts.sorted { byRemaining($0, $1, ascending: true) }
    case .remainingDesc:
      return accounts.sorted { byRemaining($0, $1, ascending: false) }
    }
  }



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



  private func byRemaining(_ lhs: Account, _ rhs: Account, ascending: Bool) -> Bool {
    switch (remainingPercent(of: lhs), remainingPercent(of: rhs)) {
    case (nil, nil): return byName(lhs, rhs, ascending: true)
    case (nil, _): return false
    case (_, nil): return true
    case (let l?, let r?):
      if l != r { return ascending ? l < r : l > r }
      return byName(lhs, rhs, ascending: true)
    }
  }

  private func remainingPercent(of account: Account) -> Double? {
    account.usage.secondaryRemainingPercent
  }



  private func byName(_ lhs: Account, _ rhs: Account, ascending: Bool) -> Bool {
    let l = lhs.displayName.lowercased()
    let r = rhs.displayName.lowercased()
    if l != r { return ascending ? l < r : l > r }
    return lhs.accountId < rhs.accountId
  }
}
