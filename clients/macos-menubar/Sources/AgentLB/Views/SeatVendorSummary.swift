import Foundation

struct SeatWindowCardModel: Equatable, Identifiable, Sendable {
  let id: String
  let title: String
  let detail: String?
  let remainingPercent: Double
  let resetText: String
  let sourceText: String
  let seatsText: String
  let help: String

  var valueText: String { remainingPercent <= 0 ? "Out" : Format.percent(remainingPercent) }

  var texts: [String] {
    [title, detail, valueText, resetText, sourceText, seatsText].compactMap { $0 }
  }
}

struct SeatVendorSummary: Equatable, Sendable {
  let scope: ProviderScope
  let cards: [SeatWindowCardModel]
  let quietLine: String?
  let seatsText: String
  let stripLines: [String]

  var texts: [String] {
    cards.flatMap(\.texts) + [quietLine, seatsText].compactMap { $0 } + stripLines
  }

  static func build(
    scope: ProviderScope, pools: [PoolEntry], seats: [SeatAccount], summary: UsageSummary?, now: Date
  ) -> SeatVendorSummary {
    let vendorSeats = scope.filter(seats)
    let vendorPools = pools.filter {
      let provider = $0.provider.lowercased()
      return (provider == scope.rawValue || (scope == .cursor && provider == "grok"))
        && ($0.kind == "cli_seat" || $0.kind == "cli_seat_budget")
    }
    let usable = vendorPools.isEmpty
      ? vendorSeats.filter { $0.enabled && $0.ready && $0.displayStatus != "reauth_required" }.count
      : vendorPools.map { $0.usableAccounts ?? $0.eligibleAccounts }.max() ?? 0
    let total = vendorPools.isEmpty
      ? vendorSeats.count
      : vendorPools.map { $0.totalAccounts ?? $0.accounts }.max() ?? 0
    let seatsText = "\(usable)/\(total) " + (total == 1 ? "seat" : "seats")

    var seen: [String: Int] = [:]
    var picked: [(PoolWindow, PoolEntry)] = []
    for pool in vendorPools {
      for window in windows(of: pool) {
        if let index = seen[window.id] {
          if pool.id == window.id, picked[index].1.id != window.id {
            picked[index] = (window, pool)
          }
        } else {
          seen[window.id] = picked.count
          picked.append((window, pool))
        }
      }
    }

    let cards = picked.compactMap { window, pool -> SeatWindowCardModel? in
      guard let remaining = window.remainingPercent else { return nil }
      let clamped = pool.status == "exhausted" ? 0 : min(100, max(0, remaining))
      let poolUsable = pool.usableAccounts ?? pool.eligibleAccounts
      let poolTotal = pool.totalAccounts ?? pool.accounts
      let cardSeats = "\(poolUsable)/\(poolTotal) " + (poolTotal == 1 ? "seat" : "seats")
      let reset = window.resetsAt.map { "resets \(clamped <= 0 ? Format.easternDay($0) : Format.easternMoment($0, relativeTo: now))" } ?? "reset not reported"
      let source = sourceText(window: window, seats: vendorSeats, now: now)
      let detail = detailText(window)
      let help = [
        window.label,
        "\(Format.percent(clamped)) left",
        window.usedPercent.map { "\(Format.percent($0)) used" },
        window.resetsAt.map { "resets \(Format.easternDateTime($0)) ET" },
        source,
        window.measuredAccounts.map { "\($0) measured" },
      ].compactMap { $0 }.joined(separator: " · ")
      return SeatWindowCardModel(
        id: window.id,
        title: title(window),
        detail: detail,
        remainingPercent: clamped,
        resetText: reset,
        sourceText: source,
        seatsText: cardSeats,
        help: help
      )
    }

    var quiet: String?
    if cards.isEmpty {
      let runs = vendorSeats.isEmpty
        ? vendorPools.compactMap(\.observedRuns).max() ?? 0
        : vendorSeats.reduce(0) { $0 + $1.todayRuns }
      let reason = vendorSeats.compactMap { $0.quota?.unavailableReason }.first
      let lead = vendorSeats.isEmpty && vendorPools.isEmpty
        ? "No \(scope.label) seats"
        : reasonText(reason, vendor: scope.label)
      quiet = "\(scope == .muse ? "Usage based" : lead) · \(runs) \(runs == 1 ? "run" : "runs") in 24 h"
    }

    return SeatVendorSummary(
      scope: scope,
      cards: cards,
      quietLine: quiet,
      seatsText: seatsText,
      stripLines: stripLines(summary: summary)
    )
  }

  static func stripLines(summary: UsageSummary?) -> [String] {
    var lines: [String] = []
    if let activity = summary?.seatActivity {
      let days = max(1, activity.periodHours / 24)
      var parts = ["\(days)d", "\(Format.compact(activity.runs)) \(activity.runs == 1 ? "run" : "runs")"]
      if activity.okKnown == false { parts.append("ok not recorded") }
      if activity.tokensKnown, let tokensIn = activity.tokensIn, let tokensOut = activity.tokensOut {
        parts.append("\(Format.compactLarge(tokensIn)) in / \(Format.compactLarge(tokensOut)) out")
      } else {
        parts.append("tokens not reported")
      }
      if let value = activity.valueUsd {
        parts.append(Format.dollars(value) + (activity.valueSource == "estimate" ? " est" : ""))
      } else {
        parts.append("Not reported")
      }
      lines.append(parts.joined(separator: " · "))
    } else {
      lines.append("7d · seat activity not reported")
    }
    if let requests = summary?.metrics?.requests7d, requests > 0 {
      var parts = ["proxy 7d", "\(Format.compact(requests)) req"]
      if let cost = summary?.cost?.totalUsd7d, cost > 0 {
        parts.append(Format.dollars(cost))
      }
      if let rate = summary?.metrics?.errorRate7d {
        parts.append("err \(String(format: "%.2f", rate * 100))%")
      }
      lines.append(parts.joined(separator: " · "))
    }
    return lines
  }

  static func reasonText(_ reason: String?, vendor: String) -> String {
    switch reason {
    case "not_reported": "Not reported by \(vendor)"
    case "auth_unsupported": "Login can't read usage"
    case "credential_unavailable": "Usage login unavailable"
    case "vendor_error": "\(vendor) usage check failed"
    case "vendor_parse": "Usage reading failed"
    case "not_collected": "Not checked yet"
    default: "Not reported by \(vendor)"
    }
  }

  private static func windows(of pool: PoolEntry) -> [PoolWindow] {
    if let windows = pool.windows { return windows }
    guard pool.kind == "cli_seat_budget", let used = pool.percentUsed else { return [] }
    let label = pool.id == "cursor-models" ? "Composer"
      : pool.id == "cursor-other" ? "Other models" : pool.id
    return [PoolWindow(
      id: pool.id,
      kind: pool.windowLabel == "week" ? "weekly" : "monthly",
      label: label,
      remainingPercent: min(100, max(0, 100 - used)),
      usedPercent: used,
      resetsAt: pool.cycleResetAt ?? pool.resetAt,
      measuredAccounts: nil,
      source: pool.percentSource
    )]
  }

  private static func kindWord(_ window: PoolWindow) -> String {
    switch window.kind {
    case "session":
      return window.label.lowercased() == "5-hour" ? "5-hour" : "session"
    case "daily": return "daily"
    case "weekly": return "weekly"
    case "monthly": return "monthly"
    default: return window.kind
    }
  }

  private static func title(_ window: PoolWindow) -> String {
    "\(kindWord(window).uppercased()) LIMIT"
  }

  private static func detailText(_ window: PoolWindow) -> String? {
    let generic = ["daily", "weekly", "monthly", "session", "5-hour", kindWord(window)]
    return generic.contains(window.label.lowercased()) ? nil : window.label
  }

  private static func sourceText(window: PoolWindow, seats: [SeatAccount], now: Date) -> String {
    if window.source == "estimate" { return "est" }
    let readings = seats.compactMap { seat -> SeatQuota? in
      guard let quota = seat.quota, quota.windows?.contains(where: { $0.id == window.id }) == true else {
        return nil
      }
      return quota
    }
    let observed = readings.compactMap(\.observedAt).max()
    let stale = readings.contains { $0.stale == true }
    var text = observed.map { "as of \(Format.easternMoment($0, relativeTo: now))" } ?? "as of —"
    if window.source == "mixed" { text = "mixed · " + text }
    if stale { text = "stale · " + text }
    return text
  }
}
