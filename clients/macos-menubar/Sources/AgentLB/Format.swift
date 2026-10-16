import Foundation

enum Format {

  nonisolated(unsafe) static let iso8601Frac: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
  }()


  nonisolated(unsafe) static let iso8601: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    return f
  }()


  static func relativeAge(_ date: Date, relativeTo now: Date = .now) -> String {
    shortAge(date, relativeTo: now) + " ago"
  }


  static func shortAge(_ date: Date, relativeTo now: Date = .now) -> String {
    let seconds = Int(max(0, now.timeIntervalSince(date)))
    if seconds < 60 { return "\(seconds)s" }
    let minutes = seconds / 60
    if minutes < 60 { return "\(minutes)m" }
    return "\(minutes / 60)h"
  }


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



  static func countdownCompact(to date: Date, relativeTo now: Date = .now) -> String {
    let seconds = max(0, date.timeIntervalSince(now))
    let totalMinutes = Int(seconds) / 60
    let hours = totalMinutes / 60
    if hours < 1 { return String(format: "0:%02d", totalMinutes) }
    if hours < 48 { return "\(hours)h" }
    return "\(hours / 24)d"
  }


  static func hhmm(_ date: Date) -> String {
    date.formatted(.dateTime.hour(.twoDigits(amPM: .omitted)).minute(.twoDigits))
  }




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


  static func dollars(_ value: Double) -> String {
    let formatter = NumberFormatter()
    formatter.locale = Locale(identifier: "en_US")
    formatter.numberStyle = .currency
    formatter.currencyCode = "USD"
    formatter.currencySymbol = "$"
    formatter.minimumFractionDigits = 2
    formatter.maximumFractionDigits = 2
    return formatter.string(from: NSNumber(value: value)) ?? "$0.00"
  }

  static let eastern = TimeZone(identifier: "America/New_York")!

  private static func easternFormatter(_ pattern: String) -> DateFormatter {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = eastern
    formatter.dateFormat = pattern
    return formatter
  }

  static func easternTime(_ date: Date) -> String {
    easternFormatter("h:mm a").string(from: date)
  }

  static func easternDateTime(_ date: Date) -> String {
    easternFormatter("MMM d, h:mm a").string(from: date)
  }

  static func easternDay(_ date: Date) -> String {
    easternFormatter("MMM d").string(from: date)
  }

  static func easternMoment(_ date: Date, relativeTo now: Date = .now) -> String {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = eastern
    return calendar.isDate(date, inSameDayAs: now) ? easternTime(date) : easternDateTime(date)
  }

  static func compact(_ value: Int) -> String {
    guard value >= 1000 else { return "\(value)" }
    let k = Double(value) / 1000.0
    let truncated = floor(k * 10) / 10
    return String(format: "%.1f", truncated) + "k"
  }



  static func percent(_ value: Double) -> String {
    if value < 100, value.rounded() >= 100 {
      return String(format: "%.1f%%", floor(value * 10) / 10)
    }
    return String(format: "%.0f%%", value)
  }


  static func latency(_ ms: Double) -> String {
    if ms >= 1000 {
      return String(format: "%.1fs", ms / 1000)
    }
    return "\(Int(ms))ms"
  }


  static func tokens(_ value: Int) -> String {
    if value >= 1000 {
      return compact(value) + " tok"
    }
    return "\(value) tok"
  }


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



  static func compactCredits(_ value: Double) -> String {
    guard value >= 1000 else { return String(format: "%.0f", value) }
    return String(format: "%.1fk", value / 1000)
  }


  static func credits(remaining: Double, capacity: Double) -> String {
    "\(compactCredits(remaining)) / \(compactCredits(capacity)) cr"
  }


  static func cachedPercent(cached: Int, total: Int) -> String? {
    guard total > 0 else { return nil }
    return percent(Double(cached) / Double(total) * 100) + " cached"
  }




  static func multiple(_ value: Double) -> String {
    if value >= 10 { return String(format: "%.0f×", value) }
    return String(format: "%.1f×", value)
  }



  static func usdCompact(_ value: Double) -> String {
    func one(_ scaled: Double) -> String { String(format: "%.1f", floor(scaled * 10) / 10) }
    if value >= 1_000_000 { return "$" + one(value / 1_000_000) + "M" }
    if value >= 1_000 { return "$" + one(value / 1_000) + "k" }
    return "$" + String(format: "%.0f", value)
  }
}
