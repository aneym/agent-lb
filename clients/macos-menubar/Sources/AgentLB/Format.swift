import Foundation

enum Format {
  // ISO-8601 with fractional seconds — e.g. "2026-06-10T20:09:28.958334Z"
  nonisolated(unsafe) static let iso8601Frac: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
  }()

  // ISO-8601 whole-second — e.g. "2026-06-10T23:50:00Z"
  nonisolated(unsafe) static let iso8601: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    return f
  }()

  // "12s ago", "2m ago", "3h ago"
  static func relativeAge(_ date: Date, relativeTo now: Date = .now) -> String {
    shortAge(date, relativeTo: now) + " ago"
  }

  // "12s", "2m", "3h" — §8.2 header sync chip and Recent-row trailing age
  static func shortAge(_ date: Date, relativeTo now: Date = .now) -> String {
    let seconds = Int(max(0, now.timeIntervalSince(date)))
    if seconds < 60 { return "\(seconds)s" }
    let minutes = seconds / 60
    if minutes < 60 { return "\(minutes)m" }
    return "\(minutes / 60)h"
  }

  // "0:15" (h:mm, < 1h) or "3h 25m" (>= 1h)
  static func countdown(to date: Date, relativeTo now: Date = .now) -> String {
    let seconds = max(0, date.timeIntervalSince(now))
    let totalMinutes = Int(seconds) / 60
    let hours = totalMinutes / 60
    let minutes = totalMinutes % 60
    if hours >= 1 {
      return "\(hours)h \(minutes)m"
    }
    return String(format: "%d:%02d", hours, minutes)
  }

  // "0:15" (< 1h), "23h" (< 48h), "6d" (≥ 48h) — §9.3 account-row window
  // grid: tighter than countdown() so two windows fit on one line.
  static func countdownCompact(to date: Date, relativeTo now: Date = .now) -> String {
    let seconds = max(0, date.timeIntervalSince(now))
    let totalMinutes = Int(seconds) / 60
    let hours = totalMinutes / 60
    if hours < 1 { return String(format: "0:%02d", totalMinutes) }
    if hours < 48 { return "\(hours)h" }
    return "\(hours / 24)d"
  }

  // "23:50" — local wall-clock time (account rows, §10 reset schedules)
  static func hhmm(_ date: Date) -> String {
    date.formatted(.dateTime.hour(.twoDigits(amPM: .omitted)).minute(.twoDigits))
  }

  // "$6,159.25" — 2 decimal places; "$0.0096" — 4 decimal when value < 0.01
  // Locale is pinned to en_US so thousand-separator and decimal-point are stable
  // regardless of system locale.
  static func usd(_ value: Double) -> String {
    let formatter = NumberFormatter()
    formatter.locale = Locale(identifier: "en_US")
    formatter.numberStyle = .currency
    formatter.currencyCode = "USD"
    formatter.currencySymbol = "$"
    if value < 0.01 {
      formatter.minimumFractionDigits = 4
      formatter.maximumFractionDigits = 4
    } else {
      formatter.minimumFractionDigits = 2
      formatter.maximumFractionDigits = 2
    }
    return formatter.string(from: NSNumber(value: value)) ?? "$0.00"
  }

  // "60.8k" (>= 1000, floor to 1 decimal) or raw integer string (< 1000)
  static func compact(_ value: Int) -> String {
    guard value >= 1000 else { return "\(value)" }
    let k = Double(value) / 1000.0
    let truncated = floor(k * 10) / 10
    return String(format: "%.1f", truncated) + "k"
  }

  // "95%" — integer percent per design §1.1 / §2.2 ("62%", "95%")
  static func percent(_ value: Double) -> String {
    String(format: "%.0f%%", value)
  }

  // "12.2s" (>= 1000 ms) or "840ms" (< 1000 ms)
  static func latency(_ ms: Double) -> String {
    if ms >= 1000 {
      return String(format: "%.1fs", ms / 1000)
    }
    return "\(Int(ms))ms"
  }

  // "713 tok" or "1.2k tok"
  static func tokens(_ value: Int) -> String {
    if value >= 1000 {
      return compact(value) + " tok"
    }
    return "\(value) tok"
  }

  // "3.8B" / "12.4M" / "60.8k" / "999" — floor to 1 decimal, like compact()
  static func compactLarge(_ value: Int) -> String {
    let v = Double(value)
    func one(_ scaled: Double) -> String {
      String(format: "%.1f", floor(scaled * 10) / 10)
    }
    switch v {
    case 1_000_000_000...: return one(v / 1_000_000_000) + "B"
    case 1_000_000...: return one(v / 1_000_000) + "M"
    case 1_000...: return one(v / 1_000) + "k"
    default: return "\(value)"
    }
  }

  // "7.5k" (>= 1000, rounded to 1 decimal — §8.2 shows 7467 cr as "7.5k")
  // or whole number below 1000.
  static func compactCredits(_ value: Double) -> String {
    guard value >= 1000 else { return String(format: "%.0f", value) }
    return String(format: "%.1fk", value / 1000)
  }

  // "7.5k / 7.8k cr" — remaining/capacity credits line on pool cards (§8.2)
  static func credits(remaining: Double, capacity: Double) -> String {
    "\(compactCredits(remaining)) / \(compactCredits(capacity)) cr"
  }

  // "80% cached" — cached share of window tokens; nil when total is 0
  static func cachedPercent(cached: Int, total: Int) -> String? {
    guard total > 0 else { return nil }
    return percent(Double(cached) / Double(total) * 100) + " cached"
  }

  // "27×" / "8.5×" — the value multiple on the §11 arbitrage line. Whole
  // number at or above 10× (the flex is already coarse there); one decimal
  // below so small pools still read a real ratio ("3.4×", not "3×").
  static func multiple(_ value: Double) -> String {
    if value >= 10 { return String(format: "%.0f×", value) }
    return String(format: "%.1f×", value)
  }

  // "$6.2k" / "$1.4M" / "$308" / "$71" — compact USD for the arbitrage line,
  // floor to 1 decimal like compact() so the flex never rounds up.
  static func usdCompact(_ value: Double) -> String {
    func one(_ scaled: Double) -> String { String(format: "%.1f", floor(scaled * 10) / 10) }
    if value >= 1_000_000 { return "$" + one(value / 1_000_000) + "M" }
    if value >= 1_000 { return "$" + one(value / 1_000) + "k" }
    return "$" + String(format: "%.0f", value)
  }
}
