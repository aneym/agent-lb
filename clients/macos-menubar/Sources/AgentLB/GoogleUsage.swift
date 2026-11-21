import Foundation

struct GoogleUsage: Decodable, Sendable, Equatable {
  let generatedAt: Date
  let plan: String?
  let requests5h: Int
  let tokens5h: Int
  let requests7d: Int
  let tokens7d: Int
  let sources: [GoogleUsageSource]

  var activeSources: [GoogleUsageSource] {
    sources.filter { $0.requests7d > 0 }
  }

  static func metricsLineCount(_ usage: GoogleUsage?) -> Int {
    2 + (usage?.activeSources.count ?? 0)
  }

  static func reportedWindow(
    _ accounts: [Account], window: ProviderScope.Window
  ) -> UsageWindow? {
    let readings = accounts.filter(\.isRoutable).compactMap { account -> (Double, Date?)? in
      let percent = window == .primary
        ? account.usage.primaryRemainingPercent : account.usage.secondaryRemainingPercent
      let reset = window == .primary ? account.resetAtPrimary : account.resetAtSecondary
      return percent.map { ($0, reset) }
    }
    guard !readings.isEmpty else { return nil }
    let average = readings.map(\.0).reduce(0, +) / Double(readings.count)
    return UsageWindow(
      remainingPercent: average,
      capacityCredits: nil,
      remainingCredits: nil,
      resetAt: readings.compactMap(\.1).min(),
      windowMinutes: window == .primary ? 300 : 10080
    )
  }
}

struct GoogleUsageSource: Decodable, Sendable, Equatable, Identifiable {
  let id: String
  let kind: String
  let name: String
  let detail: String
  let sharePercent: Double?
  let requests5h: Int
  let tokens5h: Int
  let requests7d: Int
  let tokens7d: Int
  let lastRequestAt: Date?

  var label: String {
    guard let share = sharePercent else { return "\(name) · \(detail)" }
    return "\(name) · \(Format.percent(share)) share"
  }

  var figures: String {
    "\(Format.compact(requests7d)) req · \(Format.compactLarge(tokens7d)) tok"
  }

  var help: String {
    var lines = [name, detail]
    if let share = sharePercent { lines.append("Gemini pool share \(Format.percent(share))") }
    lines.append("7 d: \(requests7d) requests · \(Format.compactLarge(tokens7d)) tokens")
    lines.append("5 h: \(requests5h) requests · \(Format.compactLarge(tokens5h)) tokens")
    if let last = lastRequestAt { lines.append("last request \(Format.relativeAge(last))") }
    return lines.joined(separator: "\n")
  }
}
