import AppKit
import SwiftUI

struct RootView: View {
  @Environment(AppState.self) private var appState
  @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
  // §9.2: top-level provider scope, persisted; scopes pool, accounts, recent.
  @AppStorage("providerScope") private var providerScopeRaw = ProviderScope.all.rawValue
  // Sizing-relevant accounts state lives HERE (not in AccountsSection): the
  // panel height is a pure function of state (PanelLayout), so RootView must
  // own every input that changes it.
  @State private var accountStatus: AccountFilter.Status = .all
  @State private var accountQuery = ""
  @State private var searchVisible = false
  @AppStorage("recentExpanded") private var recentExpanded = true

  private var scope: ProviderScope {
    ProviderScope(rawValue: providerScopeRaw) ?? .all
  }

  var body: some View {
    let layout = currentLayout
    ZStack {
      backdrop
      VStack(spacing: 0) {
        HeaderView()
        if showsScopeBar {
          ProviderScopeBar(
            scopeRaw: $providerScopeRaw,
            counts: ProviderScope.counts(in: appState.accounts)
          )
          .frame(height: PanelMetrics.scopeControl)
          .padding(.horizontal, 14)
          .padding(.bottom, 10)
        }
        Divider().opacity(0.5)
        bodyContent(layout)
        // Absorbs the (small, bounded) difference between the PanelMetrics
        // estimates and real text heights so the footer stays bottom-pinned.
        Spacer(minLength: 0)
        Divider().opacity(0.5)
        FooterBar()
      }
    }
    // Deterministic §9.1 sizing: content is pinned to the computed height
    // and PanelResizer applies the same number to the NSWindow (macOS 26
    // panels never re-measure on their own). No geometry feedback.
    .frame(height: layout.panelHeight)
    .onChange(of: layout.panelHeight) { _, height in
      PanelResizer.apply(height)
    }
    .onAppear {
      appState.popoverOpened()
      PanelResizer.apply(layout.panelHeight)
    }
    .onDisappear { appState.popoverClosed() }
  }

  // MARK: - Deterministic sizing inputs

  private var scopedAccounts: [Account] {
    scope.filter(appState.accounts)
  }

  // §9.2: Recent entries filtered to accountIds within scope; entries
  // without an accountId cannot be attributed, so they hide when scoped.
  private var scopedRecent: [RequestLogEntry] {
    guard scope != .all else { return appState.recent }
    let ids = Set(scopedAccounts.map(\.accountId))
    return appState.recent.filter { entry in
      entry.accountId.map { ids.contains($0) } ?? false
    }
  }

  /// Filtered row count for sizing — sort never changes the count, so the
  /// sort mode can stay private to AccountsSection.
  private var filteredAccountRows: Int {
    AccountFilter(provider: .all, status: accountStatus, query: accountQuery, sort: .resetSoonest)
      .apply(to: scopedAccounts, now: .now)
      .count
  }

  private var currentLayout: PanelLayout {
    var inputs = PanelLayout.Inputs()
    switch appState.serviceStatus {
    case .stopped, .unreachable, .starting:
      inputs.mode = .serviceDown
    case .running, .degraded:
      inputs.mode = hasAnyData ? .content : .loading
      inputs.degraded = appState.serviceStatus == .degraded
    }
    inputs.showsScopeBar = showsScopeBar
    inputs.metricsLines = appState.summary?.metrics?.tokensSecondaryWindow != nil ? 2 : 1
    inputs.poolHasError = appState.sectionErrors.contains(.pool)
    inputs.poolHasData = appState.summary != nil || scope != .all
    inputs.scopedAccountCount = scopedAccounts.count
    inputs.filteredAccountRows = filteredAccountRows
    inputs.searchVisible = searchVisible
    inputs.accountsHaveError = appState.sectionErrors.contains(.accounts)
    inputs.recentExpanded = recentExpanded
    inputs.recentRows = min(scopedRecent.count, 5)
    inputs.recentHasError = appState.sectionErrors.contains(.recent)
    return PanelLayout.compute(inputs)
  }

  // The scope control is chrome — visible whenever content states render.
  private var showsScopeBar: Bool {
    switch appState.serviceStatus {
    case .running, .degraded: hasAnyData
    case .starting, .stopped, .unreachable: false
    }
  }

  @ViewBuilder
  private var backdrop: some View {
    if reduceTransparency {
      Rectangle().fill(.regularMaterial).ignoresSafeArea()
    } else {
      Rectangle().glassEffect(.regular, in: .rect(cornerRadius: 0)).ignoresSafeArea()
    }
  }

  @ViewBuilder
  private func bodyContent(_ layout: PanelLayout) -> some View {
    switch appState.serviceStatus {
    case .stopped, .unreachable, .starting:
      ServiceDownView()
    case .degraded:
      VStack(spacing: 0) {
        Text("Service not ready")
          .font(.system(size: 11))
          .foregroundStyle(.secondary)
          .frame(maxWidth: .infinity)
          .frame(height: PanelMetrics.degradedBanner)
          .background(.thinMaterial)
        if hasAnyData {
          content(layout).opacity(0.7)
        } else {
          LoadingPlaceholders()
        }
      }
    case .running:
      if hasAnyData {
        content(layout)
      } else {
        LoadingPlaceholders()
      }
    }
  }

  private var hasAnyData: Bool {
    appState.summary != nil || !appState.accounts.isEmpty || appState.lastSyncAt != nil
  }

  // §8.3: panel padding 14 pt (horizontal), section vertical gap 12 pt —
  // 6 pt above + 6 pt below each section around the hairline divider.
  // §9.2: everything below the scope bar sees only scoped data.
  @ViewBuilder
  private func content(_ layout: PanelLayout) -> some View {
    VStack(spacing: 0) {
      PoolSection(
        summary: appState.summary,
        projections: appState.projections,
        scope: scope,
        scopedAccounts: scopedAccounts,
        hasError: appState.sectionErrors.contains(.pool),
        retry: retrySection
      )
      .padding(.horizontal, 14)
      .padding(.vertical, 6)
      Divider().opacity(0.5)
      AccountsSection(
        accounts: scopedAccounts,
        isScoped: scope != .all,
        listHeight: layout.listHeight,
        status: $accountStatus,
        query: $accountQuery,
        searchVisible: $searchVisible,
        hasError: appState.sectionErrors.contains(.accounts),
        retry: retrySection
      )
      .padding(.horizontal, 14)
      .padding(.vertical, 6)
      Divider().opacity(0.5)
      ActivitySection(
        entries: scopedRecent,
        hasError: appState.sectionErrors.contains(.recent),
        retry: retrySection,
        expanded: $recentExpanded
      )
      .padding(.horizontal, 14)
      .padding(.vertical, 6)
    }
    .animation(.easeOut(duration: 0.15), value: appState.summary)
    .animation(.easeOut(duration: 0.15), value: appState.accounts)
    .animation(.easeOut(duration: 0.15), value: appState.recent)
    .animation(.easeOut(duration: 0.15), value: providerScopeRaw)
  }

  private func retrySection() {
    Task { await appState.refreshNow() }
  }
}

// MARK: - Provider scope bar (§9.2)

// Full-width segmented control in the chrome area: All n / Codex n /
// Claude n. Selected segment = ink fill (.primary) with inverted label.
private struct ProviderScopeBar: View {
  @Binding var scopeRaw: String
  let counts: [ProviderScope: Int]

  var body: some View {
    HStack(spacing: 2) {
      ForEach(ProviderScope.allCases, id: \.self) { scope in
        segment(scope)
      }
    }
    .padding(2)
    .background(.thinMaterial, in: .rect(cornerRadius: 7))
  }

  private func segment(_ scope: ProviderScope) -> some View {
    let selected = scopeRaw == scope.rawValue
    return Button {
      withAnimation(.easeOut(duration: 0.15)) { scopeRaw = scope.rawValue }
    } label: {
      Text("\(scope.label) \(counts[scope] ?? 0)")
        .monospacedDigit()
        .font(.system(size: 11, weight: selected ? .semibold : .regular))
        .foregroundStyle(selected ? AnyShapeStyle(.background) : AnyShapeStyle(.secondary))
        .lineLimit(1)
        .frame(maxWidth: .infinity)
        .padding(.vertical, 4)
        .background {
          if selected {
            RoundedRectangle(cornerRadius: 5).fill(.primary)
          }
        }
        .contentShape(.rect)
    }
    .buttonStyle(.plain)
  }
}

// MARK: - Header

// §8.2: exactly two 0-wrap lines.
//   L1: ring status glyph · "Agent LB" (15 pt semibold) · spacer · ⟳ ⋯
//   L2: host chip · synced age · spacer · version (11 pt tertiary)
private struct HeaderView: View {
  @Environment(AppState.self) private var appState
  @State private var showStopConfirm = false

  var body: some View {
    // Line heights enforced per PanelMetrics so PanelLayout stays exact.
    VStack(alignment: .leading, spacing: 6) {
      HStack(alignment: .center, spacing: 8) {
        headerStatus
        Text("Agent LB")
          .font(.system(size: 15, weight: .semibold))
          .lineLimit(1)
          .fixedSize()
        Spacer(minLength: 8)
        GlassEffectContainer {
          HStack(spacing: 6) {
            refreshButton
            overflowMenu
          }
        }
        .controlSize(.small)
      }
      .frame(height: PanelMetrics.headerLine1)
      HStack(alignment: .center, spacing: 8) {
        hostChip
        syncLabel
        Spacer(minLength: 8)
        versionLabel
      }
      .frame(height: PanelMetrics.headerLine2)
    }
    .padding(.horizontal, 14)
    .padding(.top, 12)
    .padding(.bottom, 10)
    .confirmationDialog("Stop Agent LB?", isPresented: $showStopConfirm) {
      Button("Stop Anyway", role: .destructive) {
        Task { await appState.stopService() }
      }
    } message: {
      Text("The watchdog may restart the service. Stop anyway?")
    }
  }

  // §8.2 L1: a mini ring of the pool's primary remaining percent doubles as
  // the status glyph; degraded/down fall back to the §1.2 shape language.
  @ViewBuilder
  private var headerStatus: some View {
    switch appState.serviceStatus {
    case .running:
      RingGauge(
        percent: appState.summary?.primaryWindow?.remainingPercent,
        lineWidth: 2
      )
      .frame(width: 16, height: 16)
    case .degraded, .starting:
      Image(systemName: "circle.lefthalf.filled")
        .font(.system(size: 12))
        .foregroundStyle(.secondary)
        .frame(width: 16, height: 16)
    case .stopped, .unreachable:
      Image(systemName: "circle.slash")
        .font(.system(size: 12))
        .foregroundStyle(.secondary)
        .frame(width: 16, height: 16)
    }
  }

  // SHORT host only — FQDN truncated at the first dot; local mode → "local".
  private var hostChip: some View {
    Text(hostLabel)
      .font(.system(size: 10, design: .monospaced))
      .foregroundStyle(.secondary)
      .lineLimit(1)
      .padding(.horizontal, 5)
      .padding(.vertical, 1.5)
      .background(.thinMaterial, in: .rect(cornerRadius: 2))
  }

  private var hostLabel: String {
    guard appState.isRemote else { return "local" }
    let host = appState.remoteHost
    return host.split(separator: ".").first.map(String.init) ?? host
  }

  private var syncLabel: some View {
    TimelineView(.periodic(from: .now, by: 1)) { context in
      syncText(now: context.date)
        .font(.system(size: 11, design: .monospaced))
        .monospacedDigit()
        .foregroundStyle(.secondary)
        .lineLimit(1)
    }
  }

  private func syncText(now: Date) -> Text {
    switch appState.serviceStatus {
    case .running, .degraded:
      let lead: Text? = appState.serviceStatus == .degraded
        ? Text("starting / draining") : nil
      guard let syncedAt = appState.lastSyncAt else {
        return lead ?? Text("syncing…")
      }
      let synced = Text("synced \(Format.shortAge(syncedAt, relativeTo: now))")
      let stale = now.timeIntervalSince(syncedAt) > 120
      let core = stale ? synced.bold() : synced
      return lead.map { Text("\($0) · \(core)") } ?? core
    case .starting:
      return Text("starting…")
    case .stopped:
      return Text("stopped")
    case .unreachable:
      return Text("unreachable")
    }
  }

  @ViewBuilder
  private var versionLabel: some View {
    if let current = appState.version?.currentVersion {
      if appState.version?.updateAvailable == true,
         let raw = appState.version?.releaseUrl,
         let url = URL(string: raw) {
        Button {
          NSWorkspace.shared.open(url)
        } label: {
          HStack(spacing: 3) {
            Text("v\(current)").foregroundStyle(.tertiary)
            Text("↑ update").bold().foregroundStyle(.secondary)
          }
          .font(.system(size: 11))
          .lineLimit(1)
        }
        .buttonStyle(.plain)
      } else {
        Text("v\(current)")
          .font(.system(size: 11))
          .foregroundStyle(.tertiary)
          .lineLimit(1)
      }
    }
  }

  private var refreshButton: some View {
    Button {
      Task { await appState.refreshNow() }
    } label: {
      Group {
        if appState.isRefreshing {
          ProgressView()
            .controlSize(.mini)
        } else {
          Image(systemName: "arrow.clockwise")
            .font(.system(size: 12, weight: .medium))
        }
      }
      .frame(width: 16, height: 16)
    }
    .buttonStyle(.glass)
    .buttonBorderShape(.circle)
    .disabled(appState.isRefreshing)
  }

  private var overflowMenu: some View {
    Menu {
      launchAtLoginItem
      if !appState.isRemote {
        Divider()
        switch appState.serviceStatus {
        case .stopped, .unreachable:
          Button("Start Service") { Task { await appState.startService() } }
        case .running, .degraded:
          Button("Stop Service") { showStopConfirm = true }
        case .starting:
          EmptyView()
        }
      }
      Divider()
      Button("Quit Agent LB") { NSApplication.shared.terminate(nil) }
    } label: {
      Image(systemName: "ellipsis")
        .font(.system(size: 12, weight: .medium))
        .frame(width: 16, height: 16)
    }
    .menuStyle(.button)
    .menuIndicator(.hidden)
    .buttonStyle(.glass)
    .buttonBorderShape(.circle)
  }

  @ViewBuilder
  private var launchAtLoginItem: some View {
    if LaunchAtLogin.requiresApproval {
      Button("Approve in System Settings…") { LaunchAtLogin.openSettings() }
    } else {
      Toggle("Launch at Login", isOn: Binding(
        get: { LaunchAtLogin.isEnabled },
        set: { try? LaunchAtLogin.set($0) }
      ))
    }
  }
}

// MARK: - Service down / starting

private struct ServiceDownView: View {
  @Environment(AppState.self) private var appState

  var body: some View {
    VStack(spacing: 12) {
      Image(systemName: "bolt.slash")
        .font(.system(size: 28))
        .foregroundStyle(.secondary)
      Text(appState.isRemote
        ? "Can't reach Agent LB at \(appState.remoteHost)"
        : "Agent LB service is not running")
        .font(.system(size: 13))
        .multilineTextAlignment(.center)
      actionButton
      if let error = appState.startupError {
        Text(error)
          .font(.system(size: 11))
          .foregroundStyle(.secondary)
          .multilineTextAlignment(.center)
      }
    }
    .frame(maxWidth: .infinity)
    .padding(.horizontal, 12)
    // PanelMetrics.serviceDown — enforced so PanelLayout stays exact.
    .frame(height: PanelMetrics.serviceDown)
  }

  // §2.3: prominent buttons render monochrome (ink fill, inverted label
  // owned by MonochromeProminentButtonStyle) — never the system accent.
  @ViewBuilder
  private var actionButton: some View {
    if appState.isRemote {
      Button("Retry") {
        Task { await appState.refreshNow() }
      }
      .buttonStyle(.monochromeProminent)
    } else if appState.serviceStatus == .starting {
      Button {} label: {
        HStack(spacing: 6) {
          ProgressView().controlSize(.mini)
          Text("Starting…")
        }
      }
      .buttonStyle(.monochromeProminent)
      .disabled(true)
    } else {
      Button("Start Service") {
        Task { await appState.startService() }
      }
      .buttonStyle(.monochromeProminent)
    }
  }
}

// MARK: - Loading

private struct LoadingPlaceholders: View {
  var body: some View {
    VStack(spacing: 10) {
      ForEach([120.0, 180.0, 90.0], id: \.self) { height in
        RoundedRectangle(cornerRadius: 8)
          .fill(.thinMaterial)
          .frame(height: height)
      }
    }
    .padding(12)
    .redacted(reason: .placeholder)
    // PanelMetrics.loading — enforced so PanelLayout stays exact.
    .frame(height: PanelMetrics.loading)
  }
}
