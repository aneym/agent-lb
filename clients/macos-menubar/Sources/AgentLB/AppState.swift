import AppKit
import Observation

@MainActor
@Observable
final class AppState {
  enum ServiceStatus: Sendable {
    case running, degraded, starting, stopped, unreachable
  }

  enum Section: Sendable, Hashable {
    case pool, accounts, recent, seatAccounts, pools
  }

  var serviceStatus: ServiceStatus = .running
  var summary: UsageSummary?
  var projections: ProjectionsResponse?
  var accounts: [Account] = []
  var seatAccounts: [SeatAccount] = []
  var seatSource: String?
  var seatStateUpdatedAt: Date?
  var seatQuotaRefreshedAt: Date?
  var pools: [PoolEntry] = []
  var poolPlan: PoolPlan?
  var recent: [RequestLogEntry] = []
  var version: RuntimeVersion?
  var lastSyncAt: Date?
  var sectionErrors: Set<Section> = []
  var statusIcon = StatusIconRenderer.icon(for: .healthy, primaryPercent: nil, longWindowPercent: nil)


  var isRefreshing = false
  var isRestarting = false
  var startupError: String?


  var providerScope: ProviderScope = {
    let saved = UserDefaults.standard.string(forKey: "providerScope") ?? ""
    return saved == "muse" ? .other : (ProviderScope(rawValue: saved) ?? .all)
  }()

  private let client = APIClient()
  private let controller = ServiceController()
  private var closedTask: Task<Void, Never>?
  private var openTask: Task<Void, Never>?
  private var resetCreditAccountsInFlight: Set<String> = []
  private var popoverIsOpen = false
  private var lastHealthSuccess: ContinuousClock.Instant?

  nonisolated static let healthFailureGrace: Duration = .seconds(45)

  var baseURL: URL { client.base }
  var isRemote: Bool { !["127.0.0.1", "localhost", "::1"].contains(baseURL.host() ?? "") }
  var remoteHost: String { baseURL.host() ?? "remote" }
  var dashboardURL: URL { baseURL.appending(path: "dashboard") }



  func startBackgroundPolling() {
    closedTask?.cancel()
    closedTask = Task {
      var tick = 0
      while !Task.isCancelled {
        if !popoverIsOpen {
          await checkHealth()
          await fetchAccountsSilently()
          if tick.isMultiple(of: 4) {







            await fetchSummarySilently()
            await fetchMakerData(silently: true)
            await fetchRecentSilently()
            await withDiscardingTaskGroup { group in
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




  func scopeChanged(_ scope: ProviderScope) {
    providerScope = scope
    Task {
      await fetchSummary()
      recomputeStatusIcon()
    }
  }

  private func openTick(_ tick: Int) async {
    await checkHealth()
    guard serviceStatus != .stopped, serviceStatus != .unreachable else {
      recomputeStatusIcon()
      return
    }


    await fetchSummary()
    await fetchAccounts()
    await fetchMakerData()
    if tick.isMultiple(of: 2) {
      await fetchRecent()
    }
    await withDiscardingTaskGroup { group in
      if tick.isMultiple(of: 6) {
        group.addTask { await self.fetchProjections() }
        group.addTask { await self.fetchVersion() }
      }
    }
    recomputeStatusIcon()
  }



  private func checkHealth() async {
    guard serviceStatus != .starting else { return }
    await classify()
  }

  private func classify() async {
    do {
      try await client.health()
      lastHealthSuccess = .now
      let ready = (try? await client.ready()) ?? true
      serviceStatus = ready ? .running : .degraded
    } catch {
      let age = lastHealthSuccess.map { $0.duration(to: ContinuousClock.now) }
      let loaded = isRemote ? false : await controller.isLoadedWithPID()
      serviceStatus = Self.statusAfterHealthFailure(
        currentStatus: serviceStatus,
        lastSuccessAge: age,
        isRemote: isRemote,
        isLoaded: loaded
      )
    }
  }

  nonisolated static func statusAfterHealthFailure(
    currentStatus: ServiceStatus,
    lastSuccessAge: Duration?,
    isRemote: Bool,
    isLoaded: Bool
  ) -> ServiceStatus {
    if !isRemote && !isLoaded { return .stopped }
    if (currentStatus == .running || currentStatus == .degraded),
       let lastSuccessAge, lastSuccessAge < healthFailureGrace {
      return currentStatus
    }
    return .unreachable
  }








  nonisolated static func isCancellation(_ error: any Error) -> Bool {
    if error is CancellationError { return true }
    if let urlError = error as? URLError { return urlError.code == .cancelled }
    if case APIError.transport(let urlError) = error { return urlError.code == .cancelled }
    return false
  }




  nonisolated static func updateSectionError(
    _ section: Section,
    error: (any Error)?,
    in errors: inout Set<Section>
  ) {
    guard let error else {
      errors.remove(section)
      return
    }
    guard !isCancellation(error) else { return }
    errors.insert(section)
  }

  private func fetchSummary() async {
    let scope = providerScope
    do {
      let fetched = try await client.usageSummary(provider: scope.providerParam)


      guard scope == providerScope else { return }
      summary = fetched
      lastSyncAt = .now
      Self.updateSectionError(.pool, error: nil, in: &sectionErrors)
    } catch {
      guard scope == providerScope else { return }
      Self.updateSectionError(.pool, error: error, in: &sectionErrors)
    }
  }

  private func fetchAccounts() async {
    do {
      accounts = try await client.accounts().accounts
      Self.updateSectionError(.accounts, error: nil, in: &sectionErrors)
    } catch {
      Self.updateSectionError(.accounts, error: error, in: &sectionErrors)
    }
  }

  private func fetchMakerData(silently: Bool = false) async {
    do {
      let seats = try await client.seatAccounts()
      seatAccounts = SeatAccount.bySubscription(seats.accounts)
      seatSource = seats.source
      seatStateUpdatedAt = seats.stateUpdatedAt
      seatQuotaRefreshedAt = seats.quotaRefreshedAt
      Self.updateSectionError(.seatAccounts, error: nil, in: &sectionErrors)
    } catch {
      if !silently {
        Self.updateSectionError(.seatAccounts, error: error, in: &sectionErrors)
      }
    }
    do {
      pools = try await client.pools().pools
      Self.updateSectionError(.pools, error: nil, in: &sectionErrors)
    } catch {
      if !silently {
        Self.updateSectionError(.pools, error: error, in: &sectionErrors)
      }
    }
    do {
      poolPlan = try await client.poolsPlan()
    } catch {
      poolPlan = nil
    }
  }

  private func fetchRecent() async {
    do {
      recent = try await client.requestLogs(limit: 5).requests
      Self.updateSectionError(.recent, error: nil, in: &sectionErrors)
    } catch {
      Self.updateSectionError(.recent, error: error, in: &sectionErrors)
    }
  }




  private func fetchSummarySilently() async {
    let scope = providerScope
    guard let fetched = try? await client.usageSummary(provider: scope.providerParam) else { return }
    guard scope == providerScope else { return }
    summary = fetched
    lastSyncAt = .now
    Self.updateSectionError(.pool, error: nil, in: &sectionErrors)
  }



  private func fetchAccountsSilently() async {
    guard let fetched = try? await client.accounts() else { return }
    accounts = fetched.accounts
    Self.updateSectionError(.accounts, error: nil, in: &sectionErrors)
  }

  private func fetchRecentSilently() async {
    guard let fetched = try? await client.requestLogs(limit: 5) else { return }
    recent = fetched.requests
    Self.updateSectionError(.recent, error: nil, in: &sectionErrors)
  }

  private func fetchProjections() async {
    projections = (try? await client.projections()) ?? projections
  }

  private func fetchVersion() async {
    version = (try? await client.runtimeVersion()) ?? version
  }






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




  @discardableResult
  func refresh(accountId: String, via action: AccountRefreshAction) async -> Bool {



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





  @discardableResult
  func redeemResetCredit(accountId: String) async -> Bool {
    guard resetCreditAccountsInFlight.insert(accountId).inserted else {
      return false
    }
    defer { resetCreditAccountsInFlight.remove(accountId) }
    let verdict: Bool
    do {
      verdict = try await client.redeemResetCredit(accountId).didReset
    } catch {
      return false
    }
    await fetchAccounts()
    return verdict
  }



  func startService() async {
    startupError = nil
    serviceStatus = .starting
    recomputeStatusIcon()
    do {
      try await controller.start()
      lastHealthSuccess = nil
    } catch {
      startupError = "Couldn't start the service: \(error.localizedDescription)"
      await classify()
      recomputeStatusIcon()
      return
    }


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
      lastHealthSuccess = nil
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
      lastHealthSuccess = nil
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



    let percents = Self.statusIconPercents(from: summary)
    statusIcon = StatusIconRenderer.icon(
      for: state,
      primaryPercent: percents.primary,
      longWindowPercent: percents.longWindow
    )
  }





  nonisolated static func statusIconPercents(
    from summary: UsageSummary?
  ) -> (primary: Double?, longWindow: Double?) {
    let primary = summary?.primaryWindow?.remainingPercent
    let longWindow = summary?.secondaryWindow?.remainingPercent
      ?? summary?.monthlyWindow?.remainingPercent
    return (primary, longWindow)
  }
}
