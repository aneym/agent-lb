import SwiftUI

// §9.2/§9.3: two separate window cards. scope == .all renders
// /api/usage/summary verbatim; a provider scope recomputes each window from
// the scoped accounts' credit sums (ProviderScope.summarizeWindow).
struct PoolSection: View {
  @Environment(\.privacyMask) private var privacyMask
  let summary: UsageSummary?
  let projections: ProjectionsResponse?
  let scope: ProviderScope
  let scopedAccounts: [Account]
  // §11: value multiple (nil when no value/priced accounts), scoped to the
  // provider filter: the numerator comes from the provider-scoped summary
  // fetch, the denominator from the scoped accounts.
  let arbitrage: ArbitrageStats?
  let hasError: Bool
  let retry: () -> Void

  private var isScoped: Bool { scope != .all }

  // Heights are enforced per PanelMetrics so PanelLayout stays exact:
  // label 14, cards 128, metric lines 14 (spacing 8/3).
  var body: some View {
    VStack(alignment: .leading, spacing: PanelMetrics.poolSpacing) {
      SectionLabel("POOL")
        .frame(height: PanelMetrics.poolLabel)
      if hasError && summary == nil && !isScoped {
        RetryRow(retry: retry)
      } else {
        cards
        metricsStrip
        if hasError {
          RetryRow(retry: retry)
        }
      }
    }
  }

  private var cards: some View {
    let now = Date.now
    let headlineAccountCount = scopedAccounts.filter { $0.isHeadlineCountable }.count
    return HStack(alignment: .top, spacing: 10) {
      WindowCard(
        title: "5-HOUR LIMIT",
        accountCount: headlineAccountCount,
        window: primaryWindow(now: now),
        recoveredCredits: recovered(.primary, now: now),
        schedule: scheduleTooltip(.primary, now: now),
        // §9.2 honesty: projections are pool-global — hide risk when scoped.
        status: isScoped ? nil : riskStatus
      )
      WindowCard(
        title: secondaryTitle,
        accountCount: headlineAccountCount,
        window: secondaryWindow(now: now),
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

  // §10: credits the pool gets back at the next reset. Knowable only when
  // the card is computed from per-account credits — scope == all renders
  // the server summary verbatim, which carries no per-reset recovery.
  private func recovered(_ window: ProviderScope.Window, now: Date) -> Double? {
    guard isScoped else { return nil }
    return ProviderScope.summarizeWindow(scopedAccounts, window: window, now: now)
      .recoveredCredits
  }

  // §10: hover schedule — `<displayName> · <HH:mm> · +<n> cr`, soonest first.
  // §12: names are swapped for pseudonyms in privacy mode.
  private func scheduleTooltip(_ window: ProviderScope.Window, now: Date) -> String {
    let byId = Dictionary(scopedAccounts.map { ($0.accountId, $0) }, uniquingKeysWith: { first, _ in first })
    return ProviderScope.resetSchedule(scopedAccounts, window: window, now: now)
      .map { entry in
        let name = byId[entry.accountId]
          .map { privacyMask.name(for: $0, real: entry.displayName) } ?? entry.displayName
        var line = "\(name) · \(Format.hhmm(entry.resetAt))"
        if let recovery = entry.recoveredCredits {
          line += " · +\(Format.compactCredits(recovery)) cr"
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

  // §4 modeling notes: unknown pace statuses default to plain weight.
  private var paceStatus: WindowCard.Status? {
    guard summary?.secondaryWindow != nil,
          let status = projections?.weeklyCreditPace?.status else { return nil }
    let emphasized = ["behind", "ahead", "danger"].contains(status)
    return .init(text: "pace: \(status)", emphasized: emphasized, warning: false)
  }

  // §8.2: two 11 pt mono lines; §9.2 honesty: these stay global numbers
  // from /api/usage/summary, so a scoped view tags them "· all providers".
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

  // §11: the vanity headline — API-equivalent token value this week over what
  // the subscriptions cost for the same 7 days. The multiple is emphasized by
  // weight (never hue, per §2.3); the breakdown stays secondary/mono.
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

// One self-contained limit card (§9.3): title + scoped account count,
// 30 pt ring beside the 26 pt percent, credits, countdown, optional status.
private struct WindowCard: View {
  struct Status {
    let text: String
    let emphasized: Bool
    let warning: Bool
  }

  let title: String
  let accountCount: Int
  let window: UsageWindow?
  /// §10: credits regained at the next reset; nil → recovery unknowable.
  let recoveredCredits: Double?
  /// §10: per-account reset schedule, one line each, soonest first.
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
      // §8.2: 30 pt ring gauge (3 pt stroke) left of the 26 pt mono percent.
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
    // PanelMetrics.poolCard — both cards render at the same fixed height
    // regardless of which optional lines are present, so PanelLayout stays
    // exact (and the two cards always align).
    .frame(height: PanelMetrics.poolCard, alignment: .top)
    .background(.thinMaterial, in: .rect(cornerRadius: 8))
    // §10: hover reveals the per-account reset schedule.
    .help(schedule)
  }

  private var lowRemaining: Bool {
    (window?.remainingPercent ?? 100) < 15
  }

  // "7.5k / 7.8k cr" — remaining/capacity credits (§8.2)
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

  // §10: `next reset in <countdown> · +<n> cr` — the summed pool never
  // resets at once, so the line names the NEXT per-account reset and what
  // it gives back. Compact countdown + 10 pt mono: typical content
  // ("next reset in 4:59 · +1.2k cr" → 179 pt) fits the 187 pt card budget
  // outright (11 pt measured 197 pt — too wide); the worst realistic case
  // ("0:59 · +252.3k cr" → 192 pt) lands on the scale belt at 0.976.
  // lineLimit(1) makes wrapping impossible at any width.
  @ViewBuilder
  private var resetLine: some View {
    if let resetAt = window?.resetAt {
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
    var text = "next reset in \(Format.countdownCompact(to: resetAt, relativeTo: now))"
    if let recoveredCredits {
      text += " · +\(Format.compactCredits(recoveredCredits)) cr"
    }
    return text
  }
}
