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

  private var isScoped: Bool { scope != .all }
  var showsPoolTiles: Bool { scope == .all && !menuPools.isEmpty }
  var displayedWindows: [ProviderScope.Window] {
    scope == .openai ? [.secondary] : [.primary, .secondary]
  }



  var body: some View {
    VStack(alignment: .leading, spacing: PanelMetrics.poolSpacing) {
      SectionLabel(showsPoolTiles ? "POOLS" : "POOL")
        .frame(height: PanelMetrics.poolLabel)
      if hasError && summary == nil && !isScoped {
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
          Text("\(Format.usd(cost)) · 7d")
        }
        if let requests = summary?.metrics?.requests7d {
          Text("\(Format.compact(requests)) req")
        }
        if let rate = summary?.metrics?.errorRate7d {
          Text("err \(String(format: "%.2f", rate * 100))%")
        }
        if isScoped {
          Text("· all providers")
            .font(.system(size: 9))
            .foregroundStyle(.tertiary)
        }
      }
      .frame(height: PanelMetrics.metricsLine)
      if let tokens = summary?.metrics?.tokensSecondaryWindow {
        HStack(spacing: 14) {
          Text("\(Format.compactLarge(tokens)) tok · 7d")
          if let cached = summary?.metrics?.cachedTokensSecondaryWindow,
             let share = Format.cachedPercent(cached: cached, total: tokens) {
            Text(share)
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
