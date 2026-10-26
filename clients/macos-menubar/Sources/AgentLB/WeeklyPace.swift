import Foundation

struct PoolPace: Decodable, Equatable, Sendable {
  let rho: Double?
  let bStar: Double?
  let bFast: Double?
  let bSlow: Double?
  let balance: Double?
  let nextRefillH: Double?
  let eligibleAccounts: Int?
  let accounts: Int?
  let stale: Bool?

  enum CodingKeys: String, CodingKey {
    case rho, balance, accounts, stale
    case bStar = "b_star"
    case bFast = "b_fast"
    case bSlow = "b_slow"
    case nextRefillH = "next_refill_h"
    case eligibleAccounts = "eligible_accounts"
  }
}

struct WeeklyPaceRow: Equatable, Sendable, Identifiable {
  let id: String
  let name: String
  let multiple: Double?
  let dryAt: Date?
  let partial: Bool
  let help: String

  static func position(_ multiple: Double) -> Double {
    min(1, max(0, (log2(max(multiple, 1e-6)) + 2) / 4))
  }

  var isOver: Bool { (multiple ?? 0) > 1.11 }

  var isIdle: Bool { (multiple ?? 1) < 0.05 }

  var multipleText: String {
    guard let multiple, !isIdle else { return "—" }
    let text = multiple >= 99.5 ? ">99×" : Format.multiple(multiple)
    return partial ? "~" + text : text
  }

  var verdict: String {
    guard let multiple else { return partial ? "learning" : "—" }
    switch multiple {
    case ..<0.05: return "idle"
    case ..<0.5: return "well under"
    case ..<0.91: return "under"
    case ...1.11: return "on pace"
    case ...1.43: return "over"
    default: return "well over"
    }
  }

  func outlook(now: Date) -> String {
    guard let dryAt else { return verdict }
    return "dry in \(Format.countdownCompact(to: dryAt, relativeTo: now))"
  }
}

enum WeeklyPace {
  private static let pools: [(id: String, name: String)] = [
    ("anthropic-general", "Claude"),
    ("openai-codex", "Codex"),
    ("grok", "Grok"),
    ("cursor", "Cursor"),
    ("devin", "Devin"),
  ]

  static func poolIDs(for scope: ProviderScope) -> [String] {
    switch scope {
    case .all: pools.map(\.id)
    case .anthropic: ["anthropic-general"]
    case .openai: ["openai-codex"]
    case .cursor: ["grok", "cursor"]
    case .devin: ["devin"]
    case .muse, .other: []
    }
  }

  static func rows(pools entries: [PoolEntry], scope: ProviderScope, now: Date) -> [WeeklyPaceRow] {
    let byID = Dictionary(entries.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
    return poolIDs(for: scope).compactMap { id in
      guard let pace = byID[id]?.pace, let name = pools.first(where: { $0.id == id })?.name else {
        return nil
      }
      return row(id: id, name: name, pace: pace, now: now)
    }
  }

  static func row(id: String, name: String, pace: PoolPace, now: Date) -> WeeklyPaceRow {
    let idle = pace.bFast == 0 && (pace.bSlow ?? 0) == 0
    let multiple = idle ? 0 : pace.rho.flatMap { $0 > 0 ? 1 / $0 : nil }
    var dryAt: Date?
    if let fast = pace.bFast, fast > 0, let balance = pace.balance, let refill = pace.nextRefillH,
       balance / fast < refill {
      dryAt = now.addingTimeInterval(balance / fast * 3600)
    }
    return WeeklyPaceRow(
      id: id,
      name: name,
      multiple: multiple,
      dryAt: dryAt,
      partial: pace.stale ?? false,
      help: help(name: name, pace: pace, multiple: multiple, dryAt: dryAt)
    )
  }

  private static func help(name: String, pace: PoolPace, multiple: Double?, dryAt: Date?) -> String {
    func rate(_ value: Double?) -> String { value.map { String(format: "%.1f/h", $0) } ?? "—" }
    var lines = [
      multiple.map { "\(name): burning \(Format.multiple($0)) the rate that lasts to reset" }
        ?? "\(name): no burn rate yet",
      "Burn \(rate(pace.bFast)) over 2h, \(rate(pace.bSlow)) over 24h · sustainable \(rate(pace.bStar))",
    ]
    if let balance = pace.balance {
      lines.append(String(format: "%.0f left above reserve", balance)
        + " · \(pace.eligibleAccounts ?? 0) of \(pace.accounts ?? 0) accounts usable")
    }
    if let dryAt {
      lines.append("Runs dry \(Format.easternMoment(dryAt)) at the 2h burn, before the next refill")
    }
    if let refill = pace.nextRefillH {
      lines.append("First refill in \(Int(refill.rounded()))h")
    }
    if pace.stale == true {
      lines.append("~ estimate: some quota readings are old or the 24h history is short")
    }
    lines.append("Units: percent of one account's week")
    return lines.joined(separator: "\n")
  }
}
