import AppKit
import SwiftUI

struct RootView: View {
  @Environment(AppState.self) private var appState
  @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

  @AppStorage("providerScope") private var providerScopeRaw = ProviderScope.all.rawValue

  @AppStorage("privacyMode") private var privacyMode = false



  @State private var accountStatus: AccountFilter.Status = .all
  @State private var accountQuery = ""
  @State private var searchVisible = false
  @AppStorage("recentExpanded") private var recentExpanded = true

  private var scope: ProviderScope {
    ProviderScope(rawValue: providerScopeRaw) ?? .all
  }



  private var privacyMask: PrivacyMask {
    PrivacyMask.build(enabled: privacyMode, accounts: appState.accounts)
  }





  private var arbitrage: ArbitrageStats? {
    ArbitrageStats.compute(
      summary: appState.summary,
      accounts: scope == .all ? appState.accounts : scope.filter(appState.accounts)
    )
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
            counts: ProviderScope.counts(in: appState.accounts, seats: appState.seatAccounts)
          )
          .frame(height: PanelMetrics.scopeControl)
          .padding(.horizontal, 14)
          .padding(.bottom, 10)
        }
        Divider().opacity(0.5)
        bodyContent(layout)


        Spacer(minLength: 0)
        Divider().opacity(0.5)
        FooterBar()
      }
    }



    .environment(\.privacyMask, privacyMask)
    .frame(height: layout.panelHeight)
    .onChange(of: layout.panelHeight) { _, height in
      PanelResizer.apply(height)
    }
    .onChange(of: providerScopeRaw) { _, _ in
      appState.scopeChanged(scope)
    }
    .onAppear {
      appState.scopeChanged(scope)
      appState.popoverOpened()
      PanelResizer.apply(layout.panelHeight)
    }
    .onDisappear { appState.popoverClosed() }
  }



  private var scopedAccounts: [Account] {
    scope.filter(appState.accounts)
  }



  private var scopedRecent: [RequestLogEntry] {
    guard scope != .all else { return appState.recent }
    let ids = Set(scopedAccounts.map(\.accountId))
    return appState.recent.filter { entry in
      entry.accountId.map { ids.contains($0) } ?? false
    }
  }



  private var filteredAccountRows: Int {
    AccountFilter(provider: .all, status: accountStatus, query: accountQuery, sort: .resetSoonest)
      .apply(to: scopedAccounts, now: .now)
      .count + makerRowCount
  }

  private var makerRowCount: Int {
    let scopes: [ProviderScope] = scope == .all ? [.cursor, .devin] : [scope]
    return scopes.reduce(0) { count, makerScope in
      let lines = MakerRows.lines(for: makerScope, seats: appState.seatAccounts, pools: appState.pools)
      return count + lines.count + (scope == .all && !lines.isEmpty ? 1 : 0)
    }
  }

  private var accountsHaveError: Bool {
    appState.sectionErrors.contains(.accounts)
      || ((scope == .all || scope == .cursor || scope == .devin)
        && (!appState.sectionErrors.isDisjoint(with: [.seatAccounts, .pools])))
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


    let hasTokenLine = appState.summary?.metrics?.tokensSecondaryWindow != nil
    inputs.metricsLines = 1 + (hasTokenLine ? 1 : 0) + (arbitrage != nil ? 1 : 0)
    inputs.poolHasError = appState.sectionErrors.contains(.pool)
    inputs.poolHasData = appState.summary != nil || scope != .all
    inputs.scopedAccountCount = scopedAccounts.count + makerRowCount
    inputs.filteredAccountRows = filteredAccountRows
    inputs.searchVisible = searchVisible
    inputs.accountsHaveError = accountsHaveError
    inputs.recentExpanded = recentExpanded
    inputs.recentRows = min(scopedRecent.count, 5)
    inputs.recentHasError = appState.sectionErrors.contains(.recent)
    return PanelLayout.compute(inputs)
  }


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
    appState.summary != nil || !appState.accounts.isEmpty || !appState.seatAccounts.isEmpty
      || !appState.pools.isEmpty || appState.lastSyncAt != nil
  }




  @ViewBuilder
  private func content(_ layout: PanelLayout) -> some View {
    VStack(spacing: 0) {
      PoolSection(
        summary: appState.summary,
        projections: appState.projections,
        scope: scope,
        scopedAccounts: scopedAccounts,
        arbitrage: arbitrage,
        hasError: appState.sectionErrors.contains(.pool),
        retry: retrySection,
        menuPools: scope == .all ? appState.pools : [],
        plan: scope == .all ? appState.poolPlan : nil
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
        hasError: accountsHaveError,
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
    .animation(.easeOut(duration: 0.15), value: privacyMode)
  }

  private func retrySection() {
    Task { await appState.refreshNow() }
  }
}





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






private struct HeaderView: View {
  @Environment(AppState.self) private var appState
  @Environment(\.privacyMask) private var privacyMask
  @AppStorage("privacyMode") private var privacyMode = false
  @State private var showStopConfirm = false

  var body: some View {

    VStack(alignment: .leading, spacing: 6) {
      HStack(alignment: .center, spacing: 8) {
        headerStatus
        Text("Agent LB")
          .font(.system(size: 15, weight: .semibold))
          .lineLimit(1)
          .fixedSize()
        Spacer(minLength: 8)
        HStack(spacing: 6) {
          privacyButton
          refreshButton
          overflowMenu
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



  @ViewBuilder
  private var headerStatus: some View {
    switch appState.serviceStatus {
    case .running:
      RingGauge(
        percent: appState.summary?.primaryWindow?.remainingPercent
          ?? appState.summary?.secondaryWindow?.remainingPercent
          ?? appState.summary?.monthlyWindow?.remainingPercent,
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
    let short = host.split(separator: ".").first.map(String.init) ?? host
    return privacyMask.host(short)
  }



  private var privacyButton: some View {
    Button {
      privacyMode.toggle()
    } label: {
      Image(systemName: privacyMode ? "eye.slash" : "eye")
        .font(.system(size: 12, weight: .medium))
        .frame(width: 22, height: 22)
        .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .help(privacyMode ? "Show account identities" : "Hide account identities for sharing")
    .accessibilityLabel(privacyMode ? "Show account identities" : "Hide account identities")
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
      .frame(width: 22, height: 22)
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .disabled(appState.isRefreshing)
    .accessibilityLabel("Refresh")
  }

  private var overflowMenu: some View {
    Menu {
      Toggle("Hide Sensitive Info", isOn: $privacyMode)
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
        .frame(width: 22, height: 22)
        .contentShape(Rectangle())
    }
    .menuStyle(.button)
    .menuIndicator(.hidden)
    .buttonStyle(.plain)
    .accessibilityLabel("More options")
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

    .frame(height: PanelMetrics.serviceDown)
  }



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

    .frame(height: PanelMetrics.loading)
  }
}
