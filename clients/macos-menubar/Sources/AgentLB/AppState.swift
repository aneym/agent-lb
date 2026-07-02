import AppKit
import Observation

@MainActor
@Observable
final class AppState {
  enum ServiceStatus: Sendable {
    case running, degraded, starting, stopped, unreachable
  }

  enum Section: Sendable, Hashable {
    case pool, accounts, recent
  }

  var serviceStatus: ServiceStatus = .running
  var summary: UsageSummary?
  var projections: ProjectionsResponse?
  var accounts: [Account] = []
  var recent: [RequestLogEntry] = []
  var version: RuntimeVersion?
  var lastSyncAt: Date?
  var sectionErrors: Set<Section> = []
  var statusIcon = StatusIconRenderer.icon(for: .healthy, percent: nil)
  var isRefreshing = false
  var isRestarting = false
  var startupError: String?

  private let client = APIClient()
  private let controller = ServiceController()
  private var closedTask: Task<Void, Never>?
  private var openTask: Task<Void, Never>?
  private var popoverIsOpen = false

  var baseURL: URL { client.base }
  var isRemote: Bool { !["127.0.0.1", "localhost", "::1"].contains(baseURL.host() ?? "") }
  var remoteHost: String { baseURL.host() ?? "remote" }
  var dashboardURL: URL { baseURL.appending(path: "dashboard") }

  // MARK: - Polling

  func startBackgroundPolling() {
    closedTask?.cancel()
    closedTask = Task {
      var tick = 0
      while !Task.isCancelled {
        if !popoverIsOpen {
          await checkHealth()
          if tick.isMultiple(of: 4) {
            // §8.1: the 120 s tick also fetches the usage summary so the
            // status-bar ring arc tracks weekly/monthly pool usage while the popover is closed.
            // Accounts + recent are kept warm too: the MenuBarExtra panel
            // sizes itself from whatever data exists at the moment it first
            // lays out and macOS 26 never re-measures it spontaneously (see
            // RootView's panel-resize driver), so the first paint after an
            // open must already have rows to size against.
            await withDiscardingTaskGroup { group in
              group.addTask { await self.fetchSummarySilently() }
              group.addTask { await self.fetchAccountsSilently() }
              group.addTask { await self.fetchRecentSilently() }
              group.addTask { await self.fetchProjections() }
              group.addTask { await self.fetchVersion() }
            }
          }
          recomputeStatusIcon()
        }
        tick += 1
        try? await Task.sleep(for: .seconds(30))
      }
    }
  }

  func popoverOpened() {
    popoverIsOpen = true
    openTask?.cancel()
    openTask = Task {
      var tick = 0
      while !Task.isCancelled {
        await openTick(tick)
        tick += 1
        try? await Task.sleep(for: .seconds(5))
      }
    }
  }

  func popoverClosed() {
    popoverIsOpen = false
    openTask?.cancel()
    openTask = nil
  }

  func refreshNow() async {
    isRefreshing = true
    await openTick(0)
    isRefreshing = false
  }

  private func openTick(_ tick: Int) async {
    await checkHealth()
    guard serviceStatus != .stopped, serviceStatus != .unreachable else {
      recomputeStatusIcon()
      return
    }
    await withDiscardingTaskGroup { group in
      group.addTask { await self.fetchSummary() }
      group.addTask { await self.fetchAccounts() }
      if tick.isMultiple(of: 2) {
        group.addTask { await self.fetchRecent() }
      }
      if tick.isMultiple(of: 6) {
        group.addTask { await self.fetchProjections() }
        group.addTask { await self.fetchVersion() }
      }
    }
    recomputeStatusIcon()
  }

  // MARK: - Health

  private func checkHealth() async {
    guard serviceStatus != .starting else { return }
    await classify()
  }

  private func classify() async {
    do {
      try await client.health()
      let ready = (try? await client.ready()) ?? true
      serviceStatus = ready ? .running : .degraded
    } catch {
      if isRemote {
        serviceStatus = .unreachable
      } else {
        serviceStatus = await controller.isLoadedWithPID() ? .unreachable : .stopped
      }
    }
  }

  // MARK: - Fetches

  private func fetchSummary() async {
    do {
      summary = try await client.usageSummary()
      lastSyncAt = .now
      sectionErrors.remove(.pool)
    } catch {
      sectionErrors.insert(.pool)
    }
  }

  private func fetchAccounts() async {
    do {
      accounts = try await client.accounts().accounts
      sectionErrors.remove(.accounts)
    } catch {
      sectionErrors.insert(.accounts)
    }
  }

  private func fetchRecent() async {
    do {
      recent = try await client.requestLogs(limit: 5).requests
      sectionErrors.remove(.recent)
    } catch {
      sectionErrors.insert(.recent)
    }
  }

  /// Closed-state summary fetch: drives the status-bar ring arc only —
  /// never touches sectionErrors (closed-state failures must not alert).
  private func fetchSummarySilently() async {
    guard let fetched = try? await client.usageSummary() else { return }
    summary = fetched
    lastSyncAt = .now
  }

  /// Closed-state warm fetches (panel pre-sizing) — silent like the above.
  private func fetchAccountsSilently() async {
    accounts = (try? await client.accounts())?.accounts ?? accounts
  }

  private func fetchRecentSilently() async {
    recent = (try? await client.requestLogs(limit: 5))?.requests ?? recent
  }

  private func fetchProjections() async {
    projections = (try? await client.projections()) ?? projections
  }

  private func fetchVersion() async {
    version = (try? await client.runtimeVersion()) ?? version
  }

  // MARK: - Account actions

  /// Returns false when the POST fails so the row can surface the failure
  /// inline; accounts are only refreshed on success (a successful follow-up
  /// GET must not mask a failed action via the fetch-oriented sectionErrors).
  @discardableResult
  func pause(accountId: String) async -> Bool {
    do {
      try await client.pauseAccount(accountId)
    } catch {
      return false
    }
    await fetchAccounts()
    return true
  }

  @discardableResult
  func reactivate(accountId: String) async -> Bool {
    do {
      try await client.reactivateAccount(accountId)
    } catch {
      return false
    }
    await fetchAccounts()
    return true
  }

  /// On-demand re-verification for one row (e.g. after resubscribing at the
  /// vendor). Same contract as pause/reactivate: false on failure — including
  /// the server's 409 `account_not_probable` — so the row hints inline.
  @discardableResult
  func refresh(accountId: String, via action: AccountRefreshAction) async -> Bool {
    // The server answers 200 even when the upstream re-check confirms the
    // account is still broken (working=false / non-2xx probe) — the success
    // hint must reflect the upstream verdict, not the HTTP action.
    let verdict: Bool
    do {
      switch action {
      case .checkSubscription:
        verdict = try await client.checkSubscription(accountId).working
      case .probe:
        let probe = try await client.probeAccount(accountId)
        verdict = (200..<300).contains(probe.probeStatusCode)
      }
    } catch {
      return false
    }
    await fetchAccounts()
    return verdict
  }

  // MARK: - Service actions

  func startService() async {
    startupError = nil
    serviceStatus = .starting
    recomputeStatusIcon()
    do {
      try await controller.start()
    } catch {
      startupError = "Couldn't start the service: \(error.localizedDescription)"
      await classify()
      recomputeStatusIcon()
      return
    }
    // Wall-clock deadline (§1.3: poll every 1 s, 30 s timeout) — iteration
    // counting would stretch past 30 s when startupComplete() itself is slow.
    let deadline = ContinuousClock.now.advanced(by: .seconds(30))
    while ContinuousClock.now < deadline {
      if (try? await client.startupComplete()) == true {
        serviceStatus = .running
        await refreshNow()
        return
      }
      try? await Task.sleep(for: .seconds(1))
    }
    startupError = "Service didn't become ready within 30 seconds."
    await classify()
    recomputeStatusIcon()
  }

  func stopService() async {
    do {
      try await controller.stop()
    } catch {
      startupError = "Couldn't stop the service: \(error.localizedDescription)"
    }
    try? await Task.sleep(for: .milliseconds(400))
    await classify()
    recomputeStatusIcon()
  }

  func restartService() async {
    isRestarting = true
    defer { isRestarting = false }
    do {
      try await controller.restart()
    } catch {
      startupError = "Couldn't restart the service: \(error.localizedDescription)"
      return
    }
    let deadline = ContinuousClock.now.advanced(by: .seconds(20))
    while ContinuousClock.now < deadline {
      do {
        try await client.health()
        serviceStatus = .running
        await refreshNow()
        return
      } catch {
        try? await Task.sleep(for: .seconds(1))
      }
    }
    await classify()
    recomputeStatusIcon()
  }

  // MARK: - Status icon

  private func recomputeStatusIcon() {
    let state: StatusIconRenderer.IconState
    switch serviceStatus {
    case .stopped, .unreachable:
      state = .down
    case .running, .degraded, .starting:
      let risk = projections?.depletionPrimary?.riskLevel
      if risk == "danger" || risk == "critical" {
        state = .risk
      } else if version?.updateAvailable == true {
        state = .update
      } else {
        state = .healthy
      }
    }
    // §8.1: the arc is the pool's weekly/monthly remaining percent. This is
    // the right glanceable signal for the menu bar: 5-hour credits are noisy,
    // weekly/monthly capacity is the strategic constraint.
    statusIcon = StatusIconRenderer.icon(
      for: state,
      percent: Self.statusIconPercent(from: summary)
    )
  }

  nonisolated static func statusIconPercent(from summary: UsageSummary?) -> Double? {
    summary?.secondaryWindow?.remainingPercent
      ?? summary?.monthlyWindow?.remainingPercent
      ?? summary?.primaryWindow?.remainingPercent
  }
}
