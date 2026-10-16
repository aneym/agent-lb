import SwiftUI




struct PoolSection: View {
  @Environment(\.privacyMask) private var privacyMask
  let summary: UsageSummary?
  let projections: ProjectionsResponse?
  let scope: ProviderScope
  let scopedAccounts: [Account]



  let arbitrage: ArbitrageStats?
  let hasError: Bool
  let retry: () -> Void
  var menuPools: [PoolEntry] = []
  var plan: PoolPlan? = nil
  var menuNow: Date = .now
  var menuTimeZone: TimeZone = .current
  var vendorPools: [PoolEntry] = []
  var seats: [SeatAccount] = []

  private var isScoped: Bool { scope != .all }
  var showsPoolTiles: Bool { scope == .all && !menuPools.isEmpty }
  var displayedWindows: [ProviderScope.Window] {
    scope == .openai ? [.secondary] : [.primary, .secondary]
  }



  var body: some View {
    VStack(alignment: .leading, spacing: PanelMetrics.poolSpacing) {
      SectionLabel(showsPoolTiles ? "POOLS" : "POOL")
        .frame(height: PanelMetrics.poolLabel)
      if scope.isSeatVendor {
        seatVendorBody
      } else if hasError && summary == nil && !isScoped {
        RetryRow(retry: retry)
      } else if showsPoolTiles {
        poolTiles
        metricsStrip
        if hasError {
          RetryRow(retry: retry)
        }
      } else {
        cards
        metricsStrip
        if hasError {
          RetryRow(retry: retry)
        }
      }
    }
  }

  var seatVendor: SeatVendorSummary {
    SeatVendorSummary.build(scope: scope, pools: vendorPools, seats: seats, summary: summary, now: menuNow)
  }

  @ViewBuilder
  private var seatVendorBody: some View {
    let vendor = seatVendor
    if vendor.cards.isEmpty {
      if scope == .muse {
        if let line = vendor.quietLine {
          Text(line).font(.system(size: 11)).foregroundStyle(.secondary)
        }
      } else {
        SeatQuietCard(vendor: vendor)
      }
    } else {
      HStack(alignment: .top, spacing: 10) {
        ForEach(vendor.cards) { card in
          SeatWindowCard(model: card)
        }
      }
    }
    VStack(alignment: .leading, spacing: PanelMetrics.metricsSpacing) {
      ForEach(Array(vendor.stripLines.enumerated()), id: \.offset) { _, line in
        Text(line)
          .frame(height: PanelMetrics.metricsLine)
      }
    }
    .font(.system(size: 11, design: .monospaced))
    .monospacedDigit()
    .foregroundStyle(.secondary)
    .lineLimit(1)
    .minimumScaleFactor(0.85)
    if hasError {
      RetryRow(retry: retry)
    }
  }

  static func metricsLineCount(scope: ProviderScope, summary: UsageSummary?, hasArbitrage: Bool) -> Int {
    if scope.isSeatVendor {
      return max(1, SeatVendorSummary.stripLines(summary: summary).count)
    }
    let hasTokenLine = summary?.metrics?.tokensSecondaryWindow != nil
      || (scope == .all && (summary?.seatActivity?.runs ?? 0) > 0)
    return 1 + (hasTokenLine ? 1 : 0) + (hasArbitrage ? 1 : 0)
  }

  private var poolTiles: some View {
    let rows = PoolMenu.rows(pools: menuPools, now: menuNow, timeZone: menuTimeZone)
    return HStack(alignment: .top, spacing: 0) {
      ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
        PoolTileView(row: row)
          .frame(maxWidth: .infinity)
      }
    }
    .padding(12)
    .frame(maxWidth: .infinity)
    .frame(height: PanelMetrics.poolCard, alignment: .center)
    .background(.thinMaterial, in: .rect(cornerRadius: 8))
    .help(PoolMenu.footer(plan: plan) ?? "")
  }

  private var cards: some View {
    let now = Date.now
    let headlineAccountCount = scopedAccounts.filter { $0.isHeadlineCountable }.count
    return HStack(alignment: .top, spacing: 10) {
      if displayedWindows.contains(.primary) {
        WindowCard(
          title: "5-HOUR LIMIT",
          accountCount: headlineAccountCount,
          window: primaryWindow(now: now),
          knownFull: knownFull(.primary),
          recoveredCredits: recovered(.primary, now: now),
          schedule: scheduleTooltip(.primary, now: now),

          status: isScoped ? nil : riskStatus
        )
      }
      WindowCard(
        title: secondaryTitle,
        accountCount: headlineAccountCount,
        window: secondaryWindow(now: now),
        knownFull: knownFull(.secondary),
        recoveredCredits: recovered(.secondary, now: now),
        schedule: scheduleTooltip(.secondary, now: now),
        status: isScoped ? nil : paceStatus
      )
    }
  }

  private var secondaryTitle: String {
    if isScoped { return "WEEKLY LIMIT" }
    return summary?.secondaryWindow == nil && summary?.monthlyWindow != nil
      ? "MONTHLY LIMIT" : "WEEKLY LIMIT"
  }

  private func primaryWindow(now: Date) -> UsageWindow? {
    isScoped
      ? ProviderScope.summarizeWindow(scopedAccounts, window: .primary, now: now).usage
      : summary?.primaryWindow
  }

  private func secondaryWindow(now: Date) -> UsageWindow? {
    isScoped
      ? ProviderScope.summarizeWindow(scopedAccounts, window: .secondary, now: now).usage
      : summary?.secondaryWindow ?? summary?.monthlyWindow
  }




  private func recovered(_ window: ProviderScope.Window, now: Date) -> Double? {
    guard isScoped else { return nil }
    return ProviderScope.summarizeWindow(scopedAccounts, window: window, now: now)
      .recoveredCredits
  }

  private func knownFull(_ window: ProviderScope.Window) -> Bool {
    if isScoped {
      return ResetDisplay.allKnownFull(scopedAccounts, window: window)
    }
    let usage = window == .primary ? summary?.primaryWindow : summary?.secondaryWindow ?? summary?.monthlyWindow
    return usage.map(ResetDisplay.isKnownFull) ?? false
  }



  private func scheduleTooltip(_ window: ProviderScope.Window, now: Date) -> String {
    let byId = Dictionary(scopedAccounts.map { ($0.accountId, $0) }, uniquingKeysWith: { first, _ in first })
    return ProviderScope.resetSchedule(scopedAccounts, window: window, now: now)
      .map { entry in
        let name = byId[entry.accountId]
          .map { privacyMask.name(for: $0, real: entry.displayName) } ?? entry.displayName
        var line = "\(name) · \(Format.hhmm(entry.resetAt))"
        if let recovery = entry.recoveredCredits, recovery > 0 {
          line += " · \(ResetDisplay.recoveryCredits(recovery))"
        }
        return line
      }
      .joined(separator: "\n")
  }

  private var riskStatus: WindowCard.Status? {
    let level = projections?.depletionPrimary?.riskLevel ?? "safe"
    let elevated = ["warning", "danger", "critical"].contains(level)
    return .init(text: "risk: \(level)", emphasized: elevated, warning: elevated)
  }


  private var paceStatus: WindowCard.Status? {
    guard summary?.secondaryWindow != nil,
          let status = projections?.weeklyCreditPace?.status else { return nil }
    let emphasized = ["behind", "ahead", "danger"].contains(status)
    return .init(text: "pace: \(status)", emphasized: emphasized, warning: false)
  }



  private var metricsStrip: some View {
    VStack(alignment: .leading, spacing: PanelMetrics.metricsSpacing) {
      HStack(alignment: .firstTextBaseline, spacing: 14) {
        if let cost = summary?.cost?.totalUsd7d {
          Text("\(Format.dollars(cost)) · 7d")
        }
        if let requests = summary?.metrics?.requests7d {
          Text("\(Format.compact(requests)) req")
        }
        if let rate = summary?.metrics?.errorRate7d {
          Text("err \(String(format: "%.2f", rate * 100))%")
        }
        if scope == .all && (summary?.cost?.totalUsd7d != nil || summary?.metrics?.requests7d != nil || summary?.metrics?.errorRate7d != nil) {
          Text("· all providers")
            .font(.system(size: 9))
            .foregroundStyle(.tertiary)
        }
      }
      .frame(height: PanelMetrics.metricsLine)
      if summary?.metrics?.tokensSecondaryWindow != nil || allTabSeatRuns != nil {
        HStack(spacing: 14) {
          if let tokens = summary?.metrics?.tokensSecondaryWindow {
            Text("\(Format.compactLarge(tokens)) tok · 7d")
            if let cached = summary?.metrics?.cachedTokensSecondaryWindow,
               let share = Format.cachedPercent(cached: cached, total: tokens) {
              Text(share)
            }
          }
          if let runs = allTabSeatRuns {
            Text("\(Format.compact(runs)) seat runs")
          }
        }
        .frame(height: PanelMetrics.metricsLine)
      }
      if let arbitrage {
        arbitrageLine(arbitrage)
      }
    }
    .font(.system(size: 11, design: .monospaced))
    .monospacedDigit()
    .foregroundStyle(.secondary)
    .lineLimit(1)
  }




  private var allTabSeatRuns: Int? {
    guard scope == .all, let runs = summary?.seatActivity?.runs, runs > 0 else { return nil }
    return runs
  }

  private func arbitrageLine(_ stats: ArbitrageStats) -> some View {
    HStack(spacing: 6) {
      Text(stats.multipleLabel)
        .fontWeight(.semibold)
        .foregroundStyle(.primary)
      Text("value · \(Format.usdCompact(stats.valueUSD)) vs \(Format.usdCompact(stats.weeklyPlanUSD))/wk")
    }
    .frame(height: PanelMetrics.metricsLine)
    .help(arbitrageTooltip(stats))
  }

  private func arbitrageTooltip(_ stats: ArbitrageStats) -> String {
    let base = """
    \(Format.usd(stats.valueUSD)) of API-equivalent token value in the weekly window \
    vs \(Format.usd(stats.weeklyPlanUSD)) of subscriptions for the same 7 days \
    (\(stats.planCount) plans, ~\(Format.usd(stats.monthlyPlanUSD))/mo). \
    Ratio \(Format.multiple(stats.multiple)).
    """
    return stats.estimated
      ? base + " Plan prices estimated from plan type."
      : base
  }
}

private struct PoolTileView: View {
  let row: PoolMenuRow

  var body: some View {
    VStack(alignment: .center, spacing: 5) {
      RingGauge(percent: row.percent, lineWidth: 3)
        .frame(width: 30, height: 30)
      Text(row.value == "out" ? "out" : row.percent.map(Format.percent) ?? "—")
        .font(.system(size: 13, weight: lowRemaining ? .bold : .semibold, design: .monospaced))
        .monospacedDigit()
        .lineLimit(1)
      Text(row.shortName)
        .font(.system(size: 10))
        .foregroundStyle(.secondary)
        .lineLimit(1)
        .minimumScaleFactor(0.75)
      Text(row.caption.isEmpty ? " " : row.caption)
        .font(.system(size: 9, design: .monospaced))
        .foregroundStyle(.tertiary)
        .lineLimit(1)
        .minimumScaleFactor(0.75)
    }
    .help(row.help)
    .accessibilityElement(children: .combine)
  }

  private var lowRemaining: Bool {
    row.value == "out" || (row.percent ?? 100) < 15
  }
}

private struct WindowCard: View {
  struct Status {
    let text: String
    let emphasized: Bool
    let warning: Bool
  }

  let title: String
  let accountCount: Int
  let window: UsageWindow?
  let knownFull: Bool

  let recoveredCredits: Double?

  let schedule: String
  let status: Status?

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text(title)
          .font(.system(size: 11, weight: .semibold))
          .tracking(0.6)
          .foregroundStyle(.secondary)
          .lineLimit(1)
        Spacer(minLength: 4)
        Text("\(accountCount) accts")
          .font(.system(size: 9, design: .monospaced))
          .monospacedDigit()
          .foregroundStyle(.tertiary)
          .lineLimit(1)
      }

      HStack(alignment: .center, spacing: 9) {
        RingGauge(percent: window?.remainingPercent, lineWidth: 3)
          .frame(width: 30, height: 30)
        Text(window?.remainingPercent.map(Format.percent) ?? "—")
          .font(.system(size: 26, weight: lowRemaining ? .bold : .semibold, design: .monospaced))
          .monospacedDigit()
          .lineLimit(1)
          .minimumScaleFactor(0.8)
      }
      VStack(alignment: .leading, spacing: 3) {
        creditsLine
        resetLine
        if let status {
          HStack(spacing: 3) {
            Text(status.text)
              .font(.system(size: 11, weight: status.emphasized ? .bold : .regular))
            if status.warning {
              Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 9, weight: .bold))
            }
          }
          .foregroundStyle(status.emphasized ? AnyShapeStyle(.primary) : AnyShapeStyle(.secondary))
        }
      }
    }
    .padding(12)
    .frame(maxWidth: .infinity, alignment: .leading)



    .frame(height: PanelMetrics.poolCard, alignment: .top)
    .background(.thinMaterial, in: .rect(cornerRadius: 8))

    .help(schedule)
  }

  private var lowRemaining: Bool {
    (window?.remainingPercent ?? 100) < 15
  }


  @ViewBuilder
  private var creditsLine: some View {
    if let remaining = window?.remainingCredits, let capacity = window?.capacityCredits {
      Text(Format.credits(remaining: remaining, capacity: capacity))
        .font(.system(size: 11, design: .monospaced))
        .monospacedDigit()
        .foregroundStyle(.secondary)
        .lineLimit(1)
    }
  }








  @ViewBuilder
  private var resetLine: some View {
    if knownFull {
      Text("Full · no reset needed")
        .font(.system(size: 10, design: .monospaced))
        .foregroundStyle(.secondary)
    } else if let resetAt = window?.resetAt {
      TimelineView(.periodic(from: .now, by: 30)) { context in
        Text(resetText(resetAt: resetAt, now: context.date))
          .monospacedDigit()
      }
      .font(.system(size: 10, design: .monospaced))
      .foregroundStyle(.secondary)
      .lineLimit(1)
      .minimumScaleFactor(0.85)
    } else {
      Text("next reset —")
        .font(.system(size: 10))
        .foregroundStyle(.secondary)
    }
  }

  private func resetText(resetAt: Date, now: Date) -> String {
    ResetDisplay.resetText(
      resetAt: resetAt, knownFull: knownFull, recoveredCredits: recoveredCredits, now: now
    )
  }
}

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
      $0.provider.lowercased() == scope.rawValue && ($0.kind == "cli_seat" || $0.kind == "cli_seat_budget")
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
    let label = pool.id == "cursor-models" ? "Cursor models · Grok, Composer"
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

private struct SeatWindowCard: View {
  let model: SeatWindowCardModel

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text(model.title)
          .font(.system(size: 11, weight: .semibold))
          .tracking(0.6)
          .foregroundStyle(.secondary)
          .lineLimit(1)
        Spacer(minLength: 4)
        Text(model.seatsText)
          .font(.system(size: 9, design: .monospaced))
          .monospacedDigit()
          .foregroundStyle(.tertiary)
          .lineLimit(1)
          .fixedSize()
      }
      HStack(alignment: .center, spacing: 9) {
        RingGauge(percent: model.remainingPercent, lineWidth: 3)
          .frame(width: 30, height: 30)
        Text(model.valueText)
          .font(.system(size: 26, weight: model.remainingPercent < 15 ? .bold : .semibold, design: .monospaced))
          .monospacedDigit()
          .lineLimit(1)
          .minimumScaleFactor(0.8)
      }
      VStack(alignment: .leading, spacing: 3) {
        if let detail = model.detail {
          Text(detail)
            .font(.system(size: 11))
            .foregroundStyle(.secondary)
            .lineLimit(1)
            .truncationMode(.tail)
        }
        Text(model.resetText)
          .font(.system(size: 10, design: .monospaced))
          .monospacedDigit()
          .foregroundStyle(.secondary)
          .lineLimit(1)
          .minimumScaleFactor(0.85)
        Text(model.sourceText)
          .font(.system(size: 10, design: .monospaced))
          .monospacedDigit()
          .foregroundStyle(.tertiary)
          .lineLimit(1)
          .minimumScaleFactor(0.85)
      }
    }
    .padding(12)
    .frame(maxWidth: .infinity, alignment: .leading)
    .frame(height: PanelMetrics.poolCard, alignment: .top)
    .background(.thinMaterial, in: .rect(cornerRadius: 8))
    .help(model.help)
    .accessibilityElement(children: .combine)
  }
}

private struct SeatQuietCard: View {
  let vendor: SeatVendorSummary

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text("USAGE")
          .font(.system(size: 11, weight: .semibold))
          .tracking(0.6)
          .foregroundStyle(.secondary)
        Spacer(minLength: 4)
        Text(vendor.seatsText)
          .font(.system(size: 9, design: .monospaced))
          .monospacedDigit()
          .foregroundStyle(.tertiary)
          .lineLimit(1)
      }
      Spacer(minLength: 0)
      Text(vendor.quietLine ?? "")
        .font(.system(size: 12))
        .foregroundStyle(.secondary)
        .lineLimit(1)
        .minimumScaleFactor(0.85)
        .frame(maxWidth: .infinity, alignment: .center)
      Spacer(minLength: 0)
    }
    .padding(12)
    .frame(maxWidth: .infinity, alignment: .leading)
    .frame(height: PanelMetrics.poolCard, alignment: .top)
    .background(.thinMaterial, in: .rect(cornerRadius: 8))
    .accessibilityElement(children: .combine)
  }
}
