import AppKit
import SwiftUI

// §8.2/§9: account list with dashboard-parity status/sort/search filtering.
// Provider scoping happens ABOVE this section (§9.2 top-level scope bar) —
// `accounts` arrives pre-scoped and the §8 provider chips are gone.
//
// Sizing-relevant state (status filter, query, search visibility) is OWNED
// BY ROOTVIEW and passed as bindings: the panel height is a pure function of
// that state (PanelLayout), so RootView must see every input. The sort mode
// (height-neutral) stays here via @AppStorage("accountSort"). `listHeight`
// is the exact PanelLayout-computed frame for the scroll area.
struct AccountsSection: View {
  @Environment(AppState.self) private var appState
  let accounts: [Account]
  let isScoped: Bool
  let listHeight: Double
  @Binding var status: AccountFilter.Status
  @Binding var query: String
  @Binding var searchVisible: Bool
  let hasError: Bool
  let retry: () -> Void

  @FocusState private var searchFocused: Bool
  @AppStorage("accountSort") private var sortRaw = AccountFilter.Sort.resetSoonest.rawValue

  private var sort: AccountFilter.Sort {
    AccountFilter.Sort(rawValue: sortRaw) ?? .resetSoonest
  }

  private var filter: AccountFilter {
    AccountFilter(provider: .all, status: status, query: query, sort: sort)
  }

  var body: some View {
    let now = Date.now
    let filtered = filter.apply(to: accounts, now: now)
    VStack(alignment: .leading, spacing: PanelMetrics.accountsSpacing) {
      headerRow(filteredCount: filtered.count)
      if searchVisible {
        searchField
      }
      if hasError && accounts.isEmpty {
        RetryRow(retry: retry)
      } else if accounts.isEmpty {
        emptyState
      } else if filtered.isEmpty {
        noMatches
      } else {
        list(filtered)
        if hasError {
          RetryRow(retry: retry)
        }
      }
    }
  }

  // MARK: - Header (filtered count · search toggle · filter menu)

  private func headerRow(filteredCount: Int) -> some View {
    HStack(spacing: 6) {
      SectionLabel("ACCOUNTS (\(filteredCount))")
      Spacer(minLength: 6)
      Button {
        withAnimation(.easeOut(duration: 0.15)) {
          if searchVisible {
            hideSearch()
          } else {
            searchVisible = true
            searchFocused = true
          }
        }
      } label: {
        searchToggleLabel
      }
      .buttonStyle(.plain)
      .accessibilityLabel(searchVisible ? "Hide search" : "Search accounts")
      filterMenu
    }
    // PanelMetrics.accountsHeader — enforced so PanelLayout stays exact.
    .frame(height: PanelMetrics.accountsHeader)
  }

  private var searchToggleLabel: some View {
    Image(systemName: "magnifyingglass")
      .font(.system(size: 11, weight: searchVisible ? .bold : .medium))
      .foregroundStyle(searchVisible ? AnyShapeStyle(.primary) : AnyShapeStyle(.secondary))
      .frame(width: 22, height: 22)
      .contentShape(.rect)
  }

  private var filterMenu: some View {
    Menu {
      Picker("Status", selection: $status) {
        Text("All").tag(AccountFilter.Status.all)
        Text("Active").tag(AccountFilter.Status.active)
        Text("Rate-limited").tag(AccountFilter.Status.rateLimited)
        Text("Paused").tag(AccountFilter.Status.paused)
        Text("Inactive").tag(AccountFilter.Status.inactive)
      }
      .pickerStyle(.inline)
      Divider()
      Picker("Sort", selection: Binding(
        get: { sort },
        set: { sortRaw = $0.rawValue }
      )) {
        Text("Reset (soonest)").tag(AccountFilter.Sort.resetSoonest)
        Text("Reset (latest)").tag(AccountFilter.Sort.resetLatest)
        Text("Name A-Z").tag(AccountFilter.Sort.nameAsc)
        Text("Name Z-A").tag(AccountFilter.Sort.nameDesc)
      }
      .pickerStyle(.inline)
    } label: {
      Image(systemName: filter.hasActiveFilters
        ? "line.3.horizontal.decrease.circle.fill"
        : "line.3.horizontal.decrease.circle")
        .font(.system(size: 13, weight: filter.hasActiveFilters ? .bold : .regular))
        .foregroundStyle(filter.hasActiveFilters ? AnyShapeStyle(.primary) : AnyShapeStyle(.secondary))
        .frame(width: 22, height: 22)
        .contentShape(.rect)
    }
    .menuStyle(.button)
    .menuIndicator(.hidden)
    .buttonStyle(.plain)
    .fixedSize()
    .accessibilityLabel(filter.hasActiveFilters ? "Filter accounts (active)" : "Filter accounts")
  }

  // MARK: - Search

  private var searchField: some View {
    HStack(spacing: 6) {
      Image(systemName: "magnifyingglass")
        .font(.system(size: 10))
        .foregroundStyle(.tertiary)
      TextField("Search accounts…", text: $query)
        .textFieldStyle(.plain)
        .font(.system(size: 12))
        .focused($searchFocused)
        .onExitCommand { hideSearch() }
    }
    .padding(.horizontal, 8)
    .frame(height: 24)
    .background(.thinMaterial, in: .rect(cornerRadius: 6))
    .onChange(of: searchFocused) { _, focused in
      // Empty + blur hides the field (§8.2).
      if !focused && query.isEmpty {
        withAnimation(.easeOut(duration: 0.15)) { searchVisible = false }
      }
    }
  }

  private func hideSearch() {
    query = ""
    searchFocused = false
    withAnimation(.easeOut(duration: 0.15)) { searchVisible = false }
  }

  // MARK: - List / empty states

  @ViewBuilder
  private func list(_ filtered: [Account]) -> some View {
    // Regression guard (v1.2 "no account rows" bug): the list renders at the
    // EXACT height PanelLayout computed — never `maxHeight`, never flexible.
    // A flexible list was the only compressible view in the panel, so any
    // window-size staleness (macOS 26 panels never re-measure) collapsed it
    // to zero while every fixed sibling rendered normally. With a fixed
    // frame the list cannot be squeezed; rows beyond it scroll (§9.1). One
    // 30 s timeline keeps the per-window countdowns fresh between fetches.
    TimelineView(.periodic(from: .now, by: 30)) { context in
      ScrollView {
        VStack(spacing: 0) {
          ForEach(filtered) { account in
            AccountRow(account: account, now: context.date)
          }
        }
        .padding(.vertical, 4)
      }
      .frame(height: listHeight)
      .background(.thinMaterial, in: .rect(cornerRadius: 8))
    }
  }

  private var noMatches: some View {
    VStack(spacing: 6) {
      Text("No accounts match")
        .font(.system(size: 12))
        .foregroundStyle(.secondary)
      Button {
        withAnimation(.easeOut(duration: 0.15)) {
          status = .all
          query = ""
        }
      } label: {
        Text("Clear filters")
          .font(.system(size: 11))
          .underline()
          .foregroundStyle(.secondary)
      }
      .buttonStyle(.plain)
    }
    .frame(maxWidth: .infinity)
    // PanelMetrics.noMatches — enforced so PanelLayout stays exact.
    .frame(height: PanelMetrics.noMatches)
    .background(.thinMaterial, in: .rect(cornerRadius: 8))
  }

  private var emptyState: some View {
    VStack(spacing: 8) {
      Image(systemName: "person.crop.circle.badge.questionmark")
        .font(.system(size: 24))
        .foregroundStyle(.secondary)
      Text(isScoped ? "No accounts for this provider" : "No accounts connected")
        .font(.system(size: 13))
      if !isScoped {
        Button {
          NSWorkspace.shared.open(appState.dashboardURL)
        } label: {
          Text("Open Dashboard to add accounts")
            .font(.system(size: 11))
            .underline()
            .foregroundStyle(.secondary)
        }
        .buttonStyle(.plain)
      }
    }
    .frame(maxWidth: .infinity)
    // PanelMetrics.emptyState — enforced so PanelLayout stays exact.
    .frame(height: PanelMetrics.emptyState)
    .background(.thinMaterial, in: .rect(cornerRadius: 8))
  }
}

// §9.3 account row (52 pt): 20 pt 5-hour ring gauge with the status glyph
// in its center (paused/deactivated only); line 2 is a two-column grid —
// one self-contained labeled cell per window:
//   5H  [meter] 62% · 0:15   |   WK  [meter] 51% · 23h
struct AccountRow: View {
  @Environment(AppState.self) private var appState
  @Environment(\.privacyMask) private var privacyMask
  let account: Account
  var now: Date = .now
  @State private var pendingAction = false
  @State private var actionFailed = false
  @State private var isHovered = false
  @State private var refreshPhase = RefreshPhase.idle

  private enum RefreshPhase: Equatable {
    case idle, inFlight, success, failure
  }

  private enum Presentation {
    case active
    case rateLimited(Date?)
    case paused
    case deactivated(reauth: Bool)
    case unsubscribed
  }

  // §1.2 override priority: operator-disabled routing state, then blocked
  // routing state. Reset metadata only adds detail to a blocked state.
  private var presentation: Presentation {
    if account.isSubscriptionCanceled { return .unsubscribed }
    if account.status == "paused" { return .paused }
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
    if case .paused = presentation { return true }
    if case .unsubscribed = presentation { return true }
    return false
  }

  @ViewBuilder
  private var leadingRing: some View {
    if pendingAction {
      ProgressView()
        .controlSize(.small)
        .frame(width: 20, height: 20)
    } else {
      ZStack {
        RingGauge(percent: account.usage.primaryRemainingPercent, lineWidth: 2)
        centerGlyph
      }
      .frame(width: 20, height: 20)
    }
  }

  // §8.2: pause/xmark only when not active; active (and rate-limited, which
  // is signalled by the bold trailing text) shows no center glyph.
  @ViewBuilder
  private var centerGlyph: some View {
    switch presentation {
    case .active, .rateLimited, .unsubscribed:
      EmptyView()
    case .paused:
      StatusGlyph(kind: .paused, size: 8)
    case .deactivated:
      StatusGlyph(kind: .deactivated, size: 8)
    }
  }

  // §1.2: duplicates keep their displayName and gain a disambiguation suffix
  // (alias/workspaceLabel when present, "·2" otherwise). §12: privacy mode
  // replaces the whole thing with a stable pseudonym (already unique per
  // account, so no disambiguation suffix is needed).
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

  @ViewBuilder
  private var planChip: some View {
    if let plan = account.planType {
      Text(plan.uppercased())
        .font(.system(size: 9, weight: .medium))
        .tracking(0.5)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 5)
        .padding(.vertical, 1)
        .background(Capsule().fill(.quaternary.opacity(0.5)))
        .lineLimit(1)
        .fixedSize()
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
    if account.provider.lowercased() == "openai", let count = account.resetCreditsAvailable {
      Text("⟲ \(count)")
        .font(.system(size: 9, weight: .medium))
        .foregroundStyle(count == 0 ? AnyShapeStyle(.tertiary) : AnyShapeStyle(.secondary))
        .padding(.horizontal, 5)
        .padding(.vertical, 1)
        .background(Capsule().fill(.quaternary.opacity(count == 0 ? 0.35 : 0.5)))
        .lineLimit(1)
        .fixedSize()
        .help("Banked rate-limit reset credits")
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

  // MARK: - Refresh control (on-demand re-verification)

  // The slot only enters the layout when the row is refreshable and reveals
  // via opacity, so hovering never shifts the title row. Unsubscribed rows
  // keep it visible — resubscribing at the vendor is the primary reason to
  // reach for it; other rows reveal it on hover.
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

  // §9.3: active rows do not show limit status unless the backend status is
  // blocked; local subscription labels are display-only and never change
  // routing classification.
  @ViewBuilder
  private var trailingStatus: some View {
    switch presentation {
    case .active:
      if let subscriptionStatus {
        Text(subscriptionStatus)
          .font(.system(size: 12))
          .foregroundStyle(.secondary)
          .lineLimit(1)
          .fixedSize()
      } else {
        EmptyView()
      }
    case .rateLimited(let reset):
      Text(rateLimitLabel(reset: reset))
        .font(.system(size: 12, weight: .bold, design: .monospaced))
        .monospacedDigit()
    case .paused:
      Text("paused")
        .font(.system(size: 12))
        .foregroundStyle(.secondary)
    case .deactivated(let reauth):
      Text(reauth ? "re-auth needed" : "inactive")
        .font(.system(size: 12))
        .foregroundStyle(.secondary)
    case .unsubscribed:
      Text("unsubscribed")
        .font(.system(size: 12))
        .foregroundStyle(.secondary)
    }
  }

  private func rateLimitLabel(reset: Date?) -> String {
    guard let reset, reset > now else { return "limited" }
    return "limited · \(Format.hhmm(reset))"
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

  // §9.3 line 2: two equal columns, one per window.
  private var windowGrid: some View {
    HStack(alignment: .center, spacing: 12) {
      WindowCell(
        label: "5H",
        percent: account.usage.primaryRemainingPercent,
        resetAt: account.resetAtPrimary,
        now: now
      )
      WindowCell(
        label: "WK",
        percent: account.usage.secondaryRemainingPercent,
        resetAt: account.resetAtSecondary,
        now: now
      )
    }
  }

  @ViewBuilder
  private var menuItems: some View {
    switch presentation {
    case .active, .rateLimited:
      Button("Pause") {
        run { await appState.pause(accountId: account.accountId) }
      }
    case .paused, .deactivated:
      Button("Reactivate") {
        run { await appState.reactivate(accountId: account.accountId) }
      }
    case .unsubscribed:
      EmptyView()
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

// One self-contained window cell: `5H  [meter] 62% · 0:15`.
// Label 9 pt caps .secondary, 56 pt MonoMeter, mono digits, compact
// countdown to that window's reset; "—" when the window is missing.
private struct WindowCell: View {
  let label: String
  let percent: Double?
  let resetAt: Date?
  let now: Date

  var body: some View {
    HStack(spacing: 5) {
      Text(label)
        .font(.system(size: 9, weight: .semibold))
        .tracking(0.5)
        .foregroundStyle(.secondary)
        .frame(width: 16, alignment: .leading)
      if percent == nil && resetAt == nil {
        Text("—")
          .font(.system(size: 10, design: .monospaced))
          .foregroundStyle(.tertiary)
      } else {
        MonoMeter(percent: percent)
          .frame(width: 56)
        Text(detail)
          .font(.system(size: 10, design: .monospaced))
          .monospacedDigit()
          .foregroundStyle(.secondary)
          .lineLimit(1)
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }

  private var detail: String {
    var parts: [String] = []
    if let percent { parts.append(Format.percent(percent)) }
    if let resetAt, resetAt > now {
      parts.append(Format.countdownCompact(to: resetAt, relativeTo: now))
    }
    return parts.isEmpty ? "—" : parts.joined(separator: " · ")
  }
}
