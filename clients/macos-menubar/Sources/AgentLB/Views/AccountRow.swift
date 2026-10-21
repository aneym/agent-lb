import AppKit
import SwiftUI

struct AccountRow: View {
  @Environment(AppState.self) private var appState
  @Environment(\.privacyMask) private var privacyMask
  let account: Account
  var now: Date = .now
  @State private var pendingAction = false
  @State private var actionFailed = false
  @State private var isHovered = false
  @State private var refreshPhase = RefreshPhase.idle
  @State private var resetPhase = RefreshPhase.idle

  private enum RefreshPhase: Equatable {
    case idle, inFlight, success, failure
  }

  private var presentation: RowPresentation {
    if account.isSubscriptionCanceled { return .unsubscribed }
    if account.status == "paused" { return .paused }
    if account.status == "exchange_uncertain" { return .reconnecting }
    if account.status == "deactivated" && (account.deactivationReason ?? "").hasPrefix("Account on hold") {
      return .onHold
    }
    if account.isDisconnected {
      let reauth = account.status == "reauth_required"
        || (account.deactivationReason ?? "").localizedCaseInsensitiveContains("auth")
      return .deactivated(reauth: reauth)
    }
    if account.status == "rate_limited" || account.status == "quota_exceeded" {
      return .rateLimited(account.rateLimitResetAt)
    }
    return .active
  }

  var body: some View {
    HStack(alignment: .center, spacing: 9) {
      leadingRing
      VStack(alignment: .leading, spacing: 4) {
        HStack(spacing: 6) {
          Text(displayName)
            .font(.system(size: 13))
            .lineLimit(1)
            .truncationMode(.middle)
          planChip
          sharedChip
          fableChip
          resetCreditsChip
          Spacer(minLength: 6)
          refreshControl
          trailing
        }
        windowGrid
      }
    }
    .frame(height: 52)
    .padding(.horizontal, 10)
    .opacity(isDimmed ? 0.55 : 1)
    .contentShape(.rect)
    .onHover { hovering in
      withAnimation(.easeOut(duration: 0.15)) { isHovered = hovering }
    }
    .contextMenu { menuItems }
    .help(account.deactivationReason ?? "")
  }

  private var isDimmed: Bool {
    presentation.isDimmed
  }

  private var isCodex: Bool { account.provider.lowercased() == "openai" }
  private var supportsBankedResetCredits: Bool {
    ["openai", "anthropic"].contains(account.provider.lowercased())
  }

  private var ringPercent: Double? {
    isCodex
      ? account.usage.secondaryRemainingPercent
      : account.usage.primaryRemainingPercent
  }

  private var leadingRing: some View {
    RowRing(percent: ringPercent, presentation: presentation, pending: pendingAction)
  }





  private var displayName: String {
    privacyMask.name(for: account, real: realDisplayName)
  }

  private var realDisplayName: String {
    guard account.isEmailDuplicate == true else { return account.displayName }
    if let tag = account.alias ?? account.workspaceLabel {
      return account.displayName + " · " + tag
    }
    return account.displayName + " ·2"
  }

  private var planChip: some View {
    PlanChip(text: account.planType)
  }

  @ViewBuilder
  private var sharedChip: some View {
    if let label = privacyMask.sharedChipText(for: account),
       let help = privacyMask.sharedChipHelp(for: account) {
      Text(label)
        .font(.system(size: 9, weight: .medium))
        .tracking(0.5)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 5)
        .padding(.vertical, 1)
        .background(Capsule().fill(.quaternary.opacity(0.5)))
        .lineLimit(1)
        .fixedSize()
        .help(help)
        .accessibilityLabel(help)
    }
  }

  @ViewBuilder
  private var fableChip: some View {
    if let availability = account.fableAvailability, let label = account.fableAvailabilityLabel {
      Text(label)
        .font(.system(size: 9, weight: availability == .out ? .bold : .medium))
        .foregroundStyle(availability == .out ? AnyShapeStyle(.primary) : AnyShapeStyle(.secondary))
        .padding(.horizontal, 5)
        .padding(.vertical, 1)
        .background(Capsule().fill(.quaternary.opacity(availability == .out ? 0.7 : 0.35)))
        .lineLimit(1)
        .fixedSize()
        .help(account.fableAvailabilityHelp ?? availability.help)
        .accessibilityLabel(account.fableAvailabilityHelp ?? availability.help)
    }
  }

  @ViewBuilder
  private var resetCreditsChip: some View {
    if supportsBankedResetCredits {
      if let count = account.resetCreditsAvailable {
        if account.canRedeemResetCredit {
          Button {
            runRedeemResetCredit()
          } label: {
            resetCreditsChipLabel(count: count)
          }
          .buttonStyle(.plain)
          .disabled(resetPhase != .idle)
          .help("Spend one banked credit to reset this account's rate limits now")
          .accessibilityLabel("Reset rate limits now")
        } else {
          resetCreditsChipLabel(count: count)
            .help("Banked rate-limit reset credits")
        }
      } else {
        Text("⟲ ?")
          .font(.system(size: 9, weight: .medium))
          .foregroundStyle(.secondary)
          .padding(.horizontal, 5)
          .padding(.vertical, 1)
          .background(Capsule().fill(.quaternary.opacity(0.5)))
          .lineLimit(1)
          .fixedSize()
          .help("Banked reset-credit inventory is unknown")
          .accessibilityLabel("Banked reset-credit inventory unknown")
      }
    }
  }

  private func resetCreditsChipLabel(count: Int) -> some View {
    let redeemable = account.canRedeemResetCredit
    return HStack(spacing: 3) {
      if resetPhase == .inFlight {
        ProgressView().controlSize(.mini)
      }
      Text(resetPhase == .failure ? "⟲ !" : "⟲ \(count)")
    }
    .font(.system(size: 9, weight: redeemable ? .semibold : .medium))
    .foregroundStyle(count == 0 ? AnyShapeStyle(.tertiary) : AnyShapeStyle(redeemable ? .primary : .secondary))
    .padding(.horizontal, 5)
    .padding(.vertical, 1)
    .background(Capsule().fill(.quaternary.opacity(count == 0 ? 0.35 : redeemable ? 0.7 : 0.5)))
    .lineLimit(1)
    .fixedSize()
    .contentShape(.capsule)
  }

  private func runRedeemResetCredit() {
    guard resetPhase == .idle else { return }
    resetPhase = .inFlight
    Task {
      let succeeded = await appState.redeemResetCredit(accountId: account.accountId)
      resetPhase = succeeded ? .success : .failure
      try? await Task.sleep(for: .seconds(2))
      resetPhase = .idle
    }
  }

  @ViewBuilder
  private var trailing: some View {
    if actionFailed {
      Text("failed")
        .font(.system(size: 12, weight: .bold, design: .monospaced))
    } else {
      trailingStatus
    }
  }







  @ViewBuilder
  private var refreshControl: some View {
    if let action = AccountRefreshAction.action(for: account) {
      Button {
        runRefresh(action)
      } label: {
        refreshGlyph
          .frame(width: 16, height: 16)
          .contentShape(.rect)
      }
      .buttonStyle(.plain)
      .disabled(refreshPhase != .idle)
      .opacity(refreshControlVisible ? 1 : 0)
      .animation(.easeOut(duration: 0.15), value: refreshPhase)
      .help(action == .checkSubscription ? "Re-check subscription" : "Probe account now")
      .accessibilityLabel(action == .checkSubscription ? "Re-check subscription" : "Probe account now")
    }
  }

  private var refreshControlVisible: Bool {
    if refreshPhase != .idle { return true }
    if case .unsubscribed = presentation { return true }
    return isHovered
  }

  @ViewBuilder
  private var refreshGlyph: some View {
    switch refreshPhase {
    case .idle:
      Image(systemName: "arrow.clockwise")
        .font(.system(size: 10, weight: .medium))
        .foregroundStyle(.secondary)
    case .inFlight:
      ProgressView()
        .controlSize(.mini)
    case .success:
      Image(systemName: "checkmark")
        .font(.system(size: 10, weight: .semibold))
        .foregroundStyle(.secondary)
    case .failure:
      Image(systemName: "exclamationmark.circle")
        .font(.system(size: 10, weight: .semibold))
        .foregroundStyle(.secondary)
    }
  }

  private func runRefresh(_ action: AccountRefreshAction) {
    guard refreshPhase == .idle else { return }
    refreshPhase = .inFlight
    Task {
      let succeeded = await appState.refresh(accountId: account.accountId, via: action)
      refreshPhase = succeeded ? .success : .failure
      try? await Task.sleep(for: .seconds(2))
      refreshPhase = .idle
    }
  }




  private var trailingStatus: some View {
    RowTrailing(presentation: presentation, text: presentation.label(
      now: now,
      activeText: subscriptionStatus,
      reset: Format.hhmm
    ))
  }

  private var subscriptionStatus: String? {
    guard let subscription = account.subscription else { return nil }
    switch subscription.status {
    case "pause_pending":
      return "pause pending"
    case "paused":
      return "sub paused"
    case "canceled":
      return "unsubscribed"
    default:
      return nil
    }
  }


  private var windowGrid: some View {
    var cells: [RowWindow] = []
    if !isCodex {
      cells.append(RowWindow(
        id: "primary",
        label: "5H",
        percent: account.usage.primaryRemainingPercent,
        knownFull: ResetDisplay.isKnownFull(account, window: .primary),
        resetAt: account.resetAtPrimary
      ))
    }
    cells.append(RowWindow(
      id: "secondary",
      label: "WK",
      percent: account.usage.secondaryRemainingPercent,
      knownFull: ResetDisplay.isKnownFull(account, window: .secondary),
      resetAt: account.resetAtSecondary
    ))
    return RowWindowGrid(cells: cells, now: now)
  }

  @ViewBuilder
  private var menuItems: some View {
    switch presentation {
    case .active, .rateLimited:
      Button("Pause") {
        run { await appState.pause(accountId: account.accountId) }
      }
    case .paused, .deactivated, .onHold:
      Button("Reactivate") {
        run { await appState.reactivate(accountId: account.accountId) }
      }
    case .unsubscribed, .reconnecting:
      EmptyView()
    }
    if account.canRedeemResetCredit {
      Button("Reset Rate Limits (\(account.resetCreditsAvailable ?? 0) banked)") {
        runRedeemResetCredit()
      }
    }
    Button("Copy Account ID") {
      NSPasteboard.general.clearContents()
      NSPasteboard.general.setString(account.accountId, forType: .string)
    }
    Button("Open in Dashboard") {
      if let url = URL(string: appState.dashboardURL.absoluteString + "#accounts") {
        NSWorkspace.shared.open(url)
      }
    }
  }

  private func run(_ action: @escaping () async -> Bool) {
    pendingAction = true
    actionFailed = false
    Task {
      let succeeded = await action()
      pendingAction = false
      if !succeeded {
        actionFailed = true
        try? await Task.sleep(for: .seconds(3))
        actionFailed = false
      }
    }
  }
}
