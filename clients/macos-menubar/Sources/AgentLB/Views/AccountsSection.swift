import AppKit
import SwiftUI










struct AccountsSection: View {
  @Environment(AppState.self) private var appState
  let accounts: [Account]
  var seats: [SeatAccount] = []
  let isScoped: Bool
  let listHeight: Double
  @Binding var status: AccountFilter.Status
  @Binding var query: String
  @Binding var searchVisible: Bool
  let hasError: Bool
  let retry: () -> Void
  var fixedNow: Date? = nil

  @FocusState private var searchFocused: Bool
  @AppStorage("accountSort") private var sortRaw = AccountFilter.Sort.resetSoonest.rawValue
  @AppStorage("allAccountSort") private var allSortRaw = AccountFilter.Sort.remainingAsc.rawValue

  private var sort: AccountFilter.Sort {
    if isScoped {
      return AccountFilter.Sort(rawValue: sortRaw) ?? .resetSoonest
    }
    return AccountFilter.Sort(rawValue: allSortRaw) ?? .remainingAsc
  }

  private var filter: AccountFilter {
    AccountFilter(provider: .all, status: status, query: query, sort: sort)
  }

  var body: some View {
    let now = fixedNow ?? Date.now
    let filtered = filter.apply(to: accounts, seats: seats, now: now)
    let isEmpty = accounts.isEmpty && seats.isEmpty
    VStack(alignment: .leading, spacing: PanelMetrics.accountsSpacing) {
      headerRow(filteredCount: filtered.count)
      if searchVisible {
        searchField
      }
      if hasError && isEmpty {
        RetryRow(retry: retry)
      } else if isEmpty {
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
        set: {
          if isScoped {
            sortRaw = $0.rawValue
          } else {
            allSortRaw = $0.rawValue
          }
        }
      )) {
        Text("Weekly remaining (lowest)").tag(AccountFilter.Sort.remainingAsc)
        Text("Weekly remaining (highest)").tag(AccountFilter.Sort.remainingDesc)
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



  @ViewBuilder
  private func list(_ filtered: [AccountListItem]) -> some View {







    TimelineView(.periodic(from: .now, by: 30)) { context in
      let rows = VStack(spacing: 0) {
        ForEach(filtered) { item in
          switch item {
          case .proxy(let account):
            AccountRow(account: account, now: fixedNow ?? context.date)
          case .seat(let seat):
            SeatAccountRow(seat: seat, now: fixedNow ?? context.date)
          }
        }
      }
      .padding(.vertical, 4)
      Group {
        if Double(filtered.count) * PanelMetrics.accountRow + PanelMetrics.listPadding <= listHeight {
          rows.frame(maxHeight: .infinity, alignment: .top)
        } else {
          ScrollView { rows }
        }
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

    .frame(height: PanelMetrics.emptyState)
    .background(.thinMaterial, in: .rect(cornerRadius: 8))
  }
}




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
    case .paused, .deactivated:
      Button("Reactivate") {
        run { await appState.reactivate(accountId: account.accountId) }
      }
    case .unsubscribed:
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

enum RowPresentation: Equatable, Sendable {
  case active
  case rateLimited(Date?)
  case paused
  case reconnecting
  case deactivated(reauth: Bool)
  case unsubscribed

  static func forSeat(_ seat: SeatAccount) -> RowPresentation {
    switch seat.displayStatus {
    case "rate_limited": .rateLimited(seat.displayStatusResetAt)
    case "paused": .paused
    case "exchange_uncertain": .reconnecting
    case "reauth_required": .deactivated(reauth: true)
    default: .active
    }
  }

  var isDimmed: Bool {
    switch self {
    case .paused, .unsubscribed: true
    default: false
    }
  }

  func label(now: Date, activeText: String?, reset: (Date) -> String) -> String? {
    switch self {
    case .active:
      return activeText
    case .rateLimited(let resetAt):
      guard let resetAt, resetAt > now else { return "limited" }
      return "limited · \(reset(resetAt))"
    case .paused:
      return "paused"
    case .reconnecting:
      return "reconnecting"
    case .deactivated(let reauth):
      return reauth ? "re-auth needed" : "inactive"
    case .unsubscribed:
      return "unsubscribed"
    }
  }
}

struct RowRing: View {
  let percent: Double?
  let presentation: RowPresentation
  var pending = false

  var drawsGauge: Bool { percent != nil && !pending }

  var body: some View {
    if pending {
      ProgressView()
        .controlSize(.small)
        .frame(width: 20, height: 20)
    } else {
      ZStack {
        if drawsGauge {
          RingGauge(percent: percent, lineWidth: 2)
        } else {
          Circle().stroke(.quaternary, style: StrokeStyle(lineWidth: 2, dash: [2, 3]))
        }
        switch presentation {
        case .active, .rateLimited, .unsubscribed:
          EmptyView()
        case .paused:
          StatusGlyph(kind: .paused, size: 8)
        case .deactivated:
          StatusGlyph(kind: .deactivated, size: 8)
        }
      }
      .frame(width: 20, height: 20)
    }
  }
}

struct PlanChip: View {
  let text: String?

  var body: some View {
    if let text, !text.isEmpty {
      Text(text.uppercased())
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
}

struct RowTrailing: View {
  let presentation: RowPresentation
  let text: String?

  var body: some View {
    if let text {
      if case .rateLimited = presentation {
        Text(text)
          .font(.system(size: 12, weight: .bold, design: .monospaced))
          .monospacedDigit()
          .lineLimit(1)
          .fixedSize()
      } else {
        Text(text)
          .font(.system(size: 12))
          .monospacedDigit()
          .foregroundStyle(.secondary)
          .lineLimit(1)
          .fixedSize()
      }
    }
  }
}

struct RowWindow: Equatable, Identifiable, Sendable {
  let id: String
  let label: String
  let percent: Double?
  var knownFull = false
  let resetAt: Date?
  var help: String? = nil
}

struct RowWindowGrid: View {
  let cells: [RowWindow]
  let now: Date
  var emptyText: String? = nil

  var body: some View {
    HStack(alignment: .center, spacing: 12) {
      if cells.isEmpty, let emptyText {
        Text(emptyText)
          .font(.system(size: 10, design: .monospaced))
          .foregroundStyle(.tertiary)
          .lineLimit(1)
          .frame(maxWidth: .infinity, alignment: .leading)
      } else {
        ForEach(cells) { cell in
          WindowCell(cell: cell, labelWidth: labelWidth, now: now)
        }
      }
    }
  }

  private var labelWidth: CGFloat {
    let longest = cells.map(\.label.count).max() ?? 2
    return max(16, CGFloat(longest) * 7.5)
  }
}

private struct WindowCell: View {
  let cell: RowWindow
  let labelWidth: CGFloat
  let now: Date

  var body: some View {
    HStack(spacing: 5) {
      Text(cell.label)
        .font(.system(size: 9, weight: .semibold))
        .tracking(0.5)
        .foregroundStyle(.secondary)
        .lineLimit(1)
        .frame(width: labelWidth, alignment: .leading)
      if cell.percent == nil && cell.resetAt == nil {
        Text("—")
          .font(.system(size: 10, design: .monospaced))
          .foregroundStyle(.tertiary)
      } else {
        MonoMeter(percent: cell.percent)
          .frame(width: 56)
        Text(detail)
          .font(.system(size: 10, design: .monospaced))
          .monospacedDigit()
          .foregroundStyle(.secondary)
          .lineLimit(1)
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .help(cell.help ?? "")
  }

  private var detail: String {
    var parts: [String] = []
    if let percent = cell.percent { parts.append(Format.percent(percent)) }
    if !cell.knownFull, let resetAt = cell.resetAt, resetAt > now {
      parts.append(Format.countdownCompact(to: resetAt, relativeTo: now))
    }
    return parts.isEmpty ? "—" : parts.joined(separator: " · ")
  }
}

struct SeatAccountRow: View {
  @Environment(\.privacyMask) private var privacyMask
  let seat: SeatAccount
  var now: Date = .now

  var body: some View {
    HStack(alignment: .center, spacing: 9) {
      RowRing(percent: seat.primaryQuotaWindow?.remainingPercent, presentation: presentation)
      VStack(alignment: .leading, spacing: 4) {
        HStack(spacing: 6) {
          Text(displayName)
            .font(.system(size: 13))
            .lineLimit(1)
            .truncationMode(.middle)
          PlanChip(text: seat.planLabel ?? seat.tier)
          if seat.quota?.stale == true {
            PlanChip(text: "stale")
          }
          Spacer(minLength: 6)
          RowTrailing(presentation: presentation, text: trailingText)
        }
        RowWindowGrid(cells: Self.cells(for: seat), now: now, emptyText: emptyText)
      }
    }
    .frame(height: 52)
    .padding(.horizontal, 10)
    .opacity(presentation.isDimmed ? 0.55 : 1)
    .contentShape(.rect)
    .contextMenu {
      Button("Copy Seat ID") {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(seat.id, forType: .string)
      }
    }
    .help(help)
  }

  var presentation: RowPresentation {
    RowPresentation.forSeat(seat)
  }

  var displayName: String {
    privacyMask.name(for: seat, real: seat.id)
  }

  var trailingText: String? {
    if seat.onlyCursorOtherLimited, presentation == .active,
      let reset = seat.cooldowns?["cursor-other"] ?? seat.quotaWindows.first(where: { $0.id == "cursor-other" })?.resetsAt {
      return "Other out until \(Format.easternDay(reset))"
    }
    return presentation.label(now: now, activeText: Self.todayText(seat)) {
      Format.easternMoment($0, relativeTo: now)
    }
  }

  var emptyText: String {
    let cutoff = now.addingTimeInterval(-7 * 24 * 60 * 60)
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = TimeZone(secondsFromGMT: 0)
    formatter.dateFormat = "yyyy-MM-dd"
    let firstDay = formatter.string(from: cutoff)
    let lastDay = formatter.string(from: now)
    let runs = (seat.daily ?? [:]).filter { $0.key > firstDay && $0.key <= lastDay }
      .values.reduce(0) { $0 + $1.values.reduce(0) { $0 + $1.runs } }
    var text = "\(runs) \(runs == 1 ? "run" : "runs") · 7d"
    if let lastRun = seat.lastRunAt {
      text += " · last run \(Format.easternTime(lastRun))"
    }
    return text
  }

  private var help: String {
    var parts = [seat.vendorLabel]
    if let email = privacyMask.email(for: seat) { parts.append(email) }
    if let observed = seat.quota?.observedAt {
      parts.append("usage as of \(Format.easternDateTime(observed)) ET")
    }
    if let models = seat.today?.models, !models.isEmpty {
      parts.append(models.map { "\($0.model) \($0.runs)" }.joined(separator: ", "))
    }
    return parts.joined(separator: " · ")
  }

  static func todayText(_ seat: SeatAccount) -> String? {
    let runs = seat.todayRuns
    guard runs > 0 || seat.today?.valueUsd != nil else { return nil }
    var text = "\(runs) \(runs == 1 ? "run" : "runs")"
    if let value = seat.today?.valueUsd {
      text += " · " + (seat.today?.valueSource == "estimate" ? "~" : "") + Format.dollars(value)
    }
    return text
  }

  static func cells(for seat: SeatAccount) -> [RowWindow] {
    seat.quotaWindows.map { window in
      RowWindow(
        id: window.id,
        label: shortLabel(window),
        percent: window.remainingPercent,
        knownFull: (window.remainingPercent ?? 0) >= 100,
        resetAt: window.resetsAt,
        help: [
          window.label,
          window.remainingPercent.map { "\(Format.percent($0)) left" },
          window.resetsAt.map { "resets \(Format.easternDateTime($0)) ET" },
          window.source == "estimate" ? "estimate" : nil,
        ].compactMap { $0 }.joined(separator: " · ")
      )
    }
  }

  static func shortLabel(_ window: SeatQuotaWindow) -> String {
    switch window.id {
    case "cursor-models": return "CURSOR"
    case "cursor-other": return "OTHER"
    default: break
    }
    switch window.kind {
    case "session": return window.durationMins == 300 ? "5H" : "SES"
    case "daily": return "DAY"
    case "weekly": return "WK"
    case "monthly": return "MO"
    default: return String(window.kind.prefix(3)).uppercased()
    }
  }
}
