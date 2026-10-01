import Foundation

enum PoolTone: Equatable, Sendable {
  case success
  case warning
  case danger
}

struct PoolMenuRow: Equatable, Sendable {
  let name: String
  let value: String
  let fraction: Double
  let tone: PoolTone
  let ticks: [Double]
  let subline: String?
  let percent: Double?
  let shortName: String
  let caption: String
  let help: String
}

enum PoolMenu {
  static let order = [
    "anthropic-general",
    "openai-codex",
    "cursor-models",
    "cursor-other",
    "devin",
  ]

  static func rows(pools: [PoolEntry], now: Date, timeZone: TimeZone) -> [PoolMenuRow] {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = timeZone
    let pairs = pools.compactMap { pool -> (String, PoolEntry)? in
      guard let key = canonicalID(pool) else { return nil }
      return (key, pool)
    }
    let byID = Dictionary(pairs, uniquingKeysWith: { existing, incoming in
      if incoming.id == canonicalID(incoming), existing.id != canonicalID(existing) {
        return incoming
      }
      return existing
    })
    return order.compactMap { id in
      byID[id].map { row(for: $0, id: id, now: now, calendar: calendar) }
    }
  }

  static func footer(plan: PoolPlan?) -> String? {
    guard let plan, let level = plan.levels[plan.recommended] else { return nil }
    guard let code = level.ladders["implement"]?.first?.model,
          let edits = level.ladders["mechanical"]?.first?.model else { return nil }
    return "Code → \(code) · Edits → \(edits)"
  }

  private static func canonicalID(_ pool: PoolEntry) -> String? {
    switch pool.id {
    case "anthropic-general", "openai-codex", "cursor-models", "cursor-other", "devin":
      return pool.id
    default:
      switch pool.provider {
      case "anthropic": return "anthropic-general"
      case "openai": return "openai-codex"
      case "devin": return "devin"
      default: return nil
      }
    }
  }

  private static func row(for pool: PoolEntry, id: String, now: Date, calendar: Calendar) -> PoolMenuRow {
    let remaining = remainingPercent(pool)
    let out = isOut(pool, remaining: remaining)
    let usable = pool.usableAccounts ?? pool.eligibleAccounts
    let total = pool.totalAccounts ?? pool.accounts
    let value: String
    let fraction: Double
    if out {
      value = "out"
      fraction = 0
    } else if let remaining {
      value = "\(percentText(remaining)) · \(usable)/\(total)"
      fraction = min(1, max(0, remaining / 100))
    } else {
      value = "— · \(usable)/\(total)"
      fraction = 0
    }
    let percent = out ? 0 : remaining.map { min(100, max(0, $0)) }
    let shortName = id == "cursor-models" ? "Cursor" : name(for: id)
    let detail = subline(pool, out: out, now: now, calendar: calendar)
    let reset = pool.cycleResetAt ?? pool.weeklyResetAt ?? pool.resetAt
    let caption = out
      ? reset.map { "back \(monthDay($0, calendar: calendar))" } ?? "\(usable)/\(total)"
      : "\(usable)/\(total)"
    let help = [
      name(for: id),
      out ? "out" : percent.map { "\(percentText($0)) left" },
      "\(usable) of \(total) accounts usable",
      detail,
    ].compactMap { $0 }.joined(separator: " · ")
    return PoolMenuRow(
      name: name(for: id),
      value: value,
      fraction: fraction,
      tone: tone(remaining: remaining, out: out),
      ticks: ticks(pool),
      subline: detail,
      percent: percent,
      shortName: shortName,
      caption: caption,
      help: help
    )
  }

  private static func name(for id: String) -> String {
    switch id {
    case "anthropic-general": "Claude"
    case "openai-codex": "OpenAI"
    case "cursor-models": "Cursor models"
    case "cursor-other": "Cursor other"
    default: "Devin"
    }
  }

  private static func remainingPercent(_ pool: PoolEntry) -> Double? {
    if pool.provider == "cursor", let used = pool.percentUsed {
      return max(0, 100 - used)
    }
    return pool.weeklyRemainingPercent ?? pool.aggregateRemainingPercent
  }

  private static func isOut(_ pool: PoolEntry, remaining: Double?) -> Bool {
    if pool.status == "exhausted" || pool.status == "out" { return true }
    if let used = pool.percentUsed, used >= 100 { return true }
    if let remaining, remaining <= 0 { return true }
    return false
  }

  private static func tone(remaining: Double?, out: Bool) -> PoolTone {
    if out { return .danger }
    guard let remaining else { return .danger }
    if remaining >= 40 { return .success }
    if remaining >= 10 { return .warning }
    return .danger
  }

  private static func ticks(_ pool: PoolEntry) -> [Double] {
    guard let end = pool.weeklyResetAt ?? pool.resetAt ?? pool.cycleResetAt else { return [] }
    let days: TimeInterval = pool.windowLabel == "month" ? 30 : 7
    let start = end.addingTimeInterval(-days * 86_400)
    let span = end.timeIntervalSince(start)
    guard span > 0 else { return [] }
    return (pool.refills ?? []).map { refill in
      min(1, max(0, refill.at.timeIntervalSince(start) / span))
    }
  }

  private static func subline(_ pool: PoolEntry, out: Bool, now: Date, calendar: Calendar) -> String? {
    if pool.percentSource == "estimate" {
      let reset = pool.cycleResetAt ?? pool.weeklyResetAt ?? pool.resetAt
      return reset.map { "estimate · resets \(monthDay($0, calendar: calendar))" } ?? "estimate"
    }
    if out, pool.provider == "cursor", let reset = pool.cycleResetAt ?? pool.resetAt {
      return "back \(monthDay(reset, calendar: calendar))"
    }
    guard let next = (pool.refills ?? []).filter({ $0.at > now }).min(by: { $0.at < $1.at }) else {
      return nil
    }
    return refillText(next, now: now, calendar: calendar)
  }

  private static func refillText(_ refill: PoolRefill, now: Date, calendar: Calendar) -> String {
    let time = clock(refill.at, calendar: calendar)
    if calendar.isDate(refill.at, inSameDayAs: now) {
      return "+\(refill.accounts) at \(time)"
    }
    return "+\(refill.accounts) \(monthDay(refill.at, calendar: calendar)), \(time)"
  }

  private static func percentText(_ value: Double) -> String {
    "\(Int(value.rounded()))%"
  }

  private static func monthDay(_ date: Date, calendar: Calendar) -> String {
    let formatter = DateFormatter()
    formatter.calendar = calendar
    formatter.timeZone = calendar.timeZone
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.dateFormat = "MMM d"
    return formatter.string(from: date)
  }

  private static func clock(_ date: Date, calendar: Calendar) -> String {
    let formatter = DateFormatter()
    formatter.calendar = calendar
    formatter.timeZone = calendar.timeZone
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.dateFormat = "h:mm a"
    return formatter.string(from: date)
  }
}
