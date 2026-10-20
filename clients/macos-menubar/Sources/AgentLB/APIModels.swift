import Foundation



private let fableScopedWeeklyQuotaKey = "anthropic_fable_scoped_weekly"

struct AccountsResponse: Decodable {
  let accounts: [Account]
}

struct Account: Decodable, Identifiable, Sendable, Equatable {
  let accountId: String
  let provider: String
  let email: String?
  let alias: String?
  let displayName: String
  let workspaceLabel: String?
  let planType: String?
  let routingPolicy: String?
  let status: String
  let fableEligible: Bool?
  let resetCreditsAvailable: Int?
  let additionalQuotas: [AccountAdditionalQuota]?
  let usage: AccountUsage

  let remainingCreditsPrimary: Double?
  let capacityCreditsPrimary: Double?
  let remainingCreditsSecondary: Double?
  let capacityCreditsSecondary: Double?
  let resetAtPrimary: Date?
  let resetAtSecondary: Date?
  let resetAtMonthly: Date?
  let rateLimitResetAt: Date?
  let lastRefreshAt: Date?
  let deactivationReason: String?
  let isEmailDuplicate: Bool?
  let subscription: AccountSubscriptionLedger?
  let ownerInstance: String?
  let isLocallyOwned: Bool?
  let ownerLabel: String?

  var id: String { accountId }
  var sharedFrom: String? {
    isLocallyOwned == false ? (ownerLabel ?? ownerInstance ?? "peer") : nil
  }
}

extension Account {
  var fableAvailability: FableAvailability? {
    guard provider.lowercased() == "anthropic", let fableEligible else { return nil }
    return fableEligible ? .available : .out
  }

  var fableRemainingPercent: Double? {
    guard provider.lowercased() == "anthropic" else { return nil }
    guard let usedPercent = additionalQuotas?
      .first(where: { $0.quotaKey == fableScopedWeeklyQuotaKey })?
      .primaryWindow?
      .usedPercent
    else {
      return nil
    }
    return min(100, max(0, 100 - usedPercent))
  }

  var fableAvailabilityLabel: String? {
    guard let availability = fableAvailability else { return nil }
    guard let remaining = fableRemainingPercent else { return availability.label }
    switch availability {
    case .available:
      return "FABLE WK \(Format.percent(remaining))"
    case .out:
      return "FABLE WK OUT \(Format.percent(remaining))"
    }
  }

  var fableAvailabilityHelp: String? {
    guard let availability = fableAvailability else { return nil }
    guard let remaining = fableRemainingPercent else { return availability.help }
    switch availability {
    case .available:
      return "Fable weekly usage available (\(Format.percent(remaining)) remaining)"
    case .out:
      return "Fable weekly usage exhausted (\(Format.percent(remaining)) remaining); Opus availability is separate"
    }
  }

  var isSubscriptionCanceled: Bool {
    subscription?.status == "canceled"
  }

  var isDisconnected: Bool {
    status == "deactivated" || status == "reauth_required" || deactivationReason != nil
  }

  var isHeadlineCountable: Bool {
    !isSubscriptionCanceled && !isDisconnected
  }

  var isRoutable: Bool {
    isHeadlineCountable && status != "paused"
  }






  var canRedeemResetCredit: Bool {
    guard ["openai", "anthropic"].contains(provider.lowercased()) else { return false }
    guard (resetCreditsAvailable ?? 0) > 0 else { return false }
    guard isRoutable else { return false }
    return status == "rate_limited" || status == "quota_exceeded"
  }
}

enum FableAvailability: Sendable, Equatable {
  case available
  case out

  var label: String {
    switch self {
    case .available: return "FABLE WK"
    case .out: return "FABLE WK OUT"
    }
  }

  var help: String {
    switch self {
    case .available: return "Fable weekly usage available"
    case .out: return "Fable weekly usage exhausted; Opus availability is separate"
    }
  }
}

struct AccountSubscriptionLedger: Decodable, Sendable, Equatable {
  let status: String?



  let amount: Double?
  let currency: String?
  let nextChargeAt: Date?
  let currentPeriodEndAt: Date?
  let lastVerifiedAt: Date?
}

struct AccountUsage: Decodable, Sendable, Equatable {
  let primaryRemainingPercent: Double?
  let secondaryRemainingPercent: Double?
  let monthlyRemainingPercent: Double?
}

struct AccountAdditionalQuota: Decodable, Sendable, Equatable {
  let quotaKey: String?
  let primaryWindow: AccountAdditionalWindow?
}

struct AccountAdditionalWindow: Decodable, Sendable, Equatable {
  let usedPercent: Double
  let resetAt: Int?
  let windowMinutes: Int?
}








enum AccountRefreshAction: Sendable, Equatable {
  case checkSubscription
  case probe

  static func action(for account: Account) -> AccountRefreshAction? {
    if account.isSubscriptionCanceled { return .checkSubscription }
    if account.status == "paused" || account.isDisconnected { return nil }
    return .probe
  }
}

struct AccountProbeResponse: Decodable, Sendable, Equatable {
  let status: String
  let accountId: String
  let probeStatusCode: Int
}





struct AccountResetCreditConsumeResponse: Decodable, Sendable, Equatable {
  let status: String
  let accountId: String
  let code: String
  let windowsReset: Int

  var didReset: Bool { status == "redeemed" }
}

struct AccountSubscriptionCheckResponse: Decodable, Sendable, Equatable {
  let status: String
  let accountId: String
  let working: Bool
  let probeStatusCode: Int
  let subscription: AccountSubscriptionLedger?
}



struct UsageSummary: Decodable, Sendable, Equatable {
  let primaryWindow: UsageWindow?
  let secondaryWindow: UsageWindow?
  let monthlyWindow: UsageWindow?
  let cost: CostSummary?
  let metrics: SummaryMetrics?
  var provider: String? = nil
  var seatActivity: SeatActivity? = nil
}

struct SeatActivity: Decodable, Sendable, Equatable {
  let periodHours: Int
  let runs: Int
  let ok: Int
  var okKnown: Bool? = nil
  var tokensIn: Int? = nil
  var tokensOut: Int? = nil
  let tokensKnown: Bool
  var valueUsd: Double? = nil
  var valueSource: String? = nil
  var models: [SeatActivityModel]? = nil
}

struct SeatActivityModel: Decodable, Sendable, Equatable {
  let model: String
  let runs: Int
}

struct UsageWindow: Decodable, Sendable, Equatable {
  let remainingPercent: Double?
  let capacityCredits: Double?
  let remainingCredits: Double?
  let resetAt: Date?
  let windowMinutes: Int?
}

struct CostSummary: Decodable, Sendable, Equatable {
  let currency: String?
  let totalUsd7d: Double?
}

struct SummaryMetrics: Decodable, Sendable, Equatable {
  let requests7d: Int?
  let errorRate7d: Double?
  let topError: String?
  let tokensSecondaryWindow: Int?
  let cachedTokensSecondaryWindow: Int?
}



struct ProjectionsResponse: Decodable, Sendable, Equatable {
  let depletionPrimary: Depletion?
  let depletionSecondary: Depletion?
  let weeklyCreditPace: WeeklyCreditPace?
}

struct Depletion: Decodable, Sendable, Equatable {
  let risk: Double?
  let riskLevel: String?
  let projectedExhaustionAt: Date?
  let secondsUntilExhaustion: Double?
}

struct WeeklyCreditPace: Decodable, Sendable, Equatable {
  let actualUsedPercent: Double?
  let scheduledUsedPercent: Double?
  let deltaPercent: Double?
  let status: String?
  let confidence: String?
}



struct RequestLogsResponse: Decodable, Sendable, Equatable {
  let requests: [RequestLogEntry]
  let total: Int
  let hasMore: Bool
}

struct RequestLogEntry: Decodable, Identifiable, Sendable, Equatable {
  let requestedAt: Date
  let requestId: String
  let accountId: String?
  let model: String?
  let status: String
  let errorCode: String?
  let tokens: Int?
  let costUsd: Double?
  let latencyMs: Double?

  var id: String { requestId }
}



struct RuntimeVersion: Decodable, Sendable, Equatable {
  let currentVersion: String?
  let latestVersion: String?
  let updateAvailable: Bool
  let releaseUrl: String?
}



struct HealthResponse: Decodable, Sendable {
  let status: String
}



struct SeatAccountsResponse: Decodable {
  let stateUpdatedAt: Date?
  let source: String
  var quotaRefreshedAt: Date? = nil
  let accounts: [SeatAccount]
}

struct SeatAccount: Decodable, Equatable, Sendable, Identifiable {
  let id: String
  let vendor: String
  var auth: String? = nil
  let enabled: Bool
  let authOk: Bool?
  var authCheckedAt: Date? = nil
  let tier: String?
  var plan: String? = nil
  var cycleDay: Int? = nil
  let cooldownUntil: Date?
  let cooldowns: [String: Date]?
  var lastErrorKind: String? = nil
  var lastErrorAt: Date? = nil
  var lastRunAt: Date? = nil
  let ready: Bool
  let lastDay: SeatUsage
  var daily: [String: [String: SeatDailyUsage]]? = nil
  var recentRuns: [SeatRun]? = nil
  var email: String? = nil
  var planLabel: String? = nil
  var status: String? = nil
  var statusResetAt: Date? = nil
  var quota: SeatQuota? = nil
  var today: SeatToday? = nil
  var logins: [String]? = nil
}

struct SeatDailyUsage: Decodable, Equatable, Sendable {
  let runs: Int
}

struct SeatUsage: Decodable, Equatable, Sendable {
  let runs: Int
  let ok: Int
  var wallS: Double? = nil
  let tokensIn: Int
  let tokensOut: Int
}

struct SeatRun: Decodable, Equatable, Sendable {
  let ts: Date
  var model: String? = nil
  var tokensIn: Int? = nil
  var tokensOut: Int? = nil
  var cacheRead: Int? = nil
  var ok: Bool? = nil
  var wallS: Double? = nil
}

struct SeatQuota: Decodable, Equatable, Sendable {
  let source: String
  var observedAt: Date? = nil
  var stale: Bool? = nil
  var unavailableReason: String? = nil
  var primaryWindowId: String? = nil
  var windows: [SeatQuotaWindow]? = nil
}

struct SeatQuotaWindow: Decodable, Equatable, Sendable {
  let id: String
  let kind: String
  let label: String
  var durationMins: Int? = nil
  var usedPercent: Double? = nil
  var remainingPercent: Double? = nil
  var resetsAt: Date? = nil
  var source: String? = nil
}

struct SeatToday: Decodable, Equatable, Sendable {
  let runs: Int
  let ok: Int
  var okKnown: Bool? = nil
  var tokensIn: Int? = nil
  var tokensOut: Int? = nil
  let tokensKnown: Bool
  var valueUsd: Double? = nil
  var valueSource: String? = nil
  var models: [SeatTodayModel]? = nil
}

struct SeatTodayModel: Decodable, Equatable, Sendable {
  let model: String
  let runs: Int
  var tokensIn: Int? = nil
  var tokensOut: Int? = nil
}

extension SeatAccount {
  var subscriptionKey: String {
    if vendor.lowercased() == "cursor", let plan { return "cursor:\(plan)" }
    return id
  }

  var displayId: String {
    guard let logins, logins.count > 1 else { return id }
    return "\(vendorLabel) · \(logins.count) logins"
  }

  static func bySubscription(_ seats: [SeatAccount]) -> [SeatAccount] {
    var order: [String] = []
    var groups: [String: [SeatAccount]] = [:]
    for seat in seats {
      if groups[seat.subscriptionKey] == nil { order.append(seat.subscriptionKey) }
      groups[seat.subscriptionKey, default: []].append(seat)
    }
    return order.compactMap { key in
      guard let group = groups[key], var primary = group.first(where: { $0.auth == "login" && $0.enabled }) ?? group.first
      else { return nil }
      if group.count > 1 { primary.logins = group.map(\.id) }
      return primary
    }
  }

  var scope: ProviderScope? {
    ProviderScope(rawValue: vendor.lowercased()).flatMap { $0.isSeatVendor ? $0 : nil }
  }

  var vendorLabel: String {
    scope?.label ?? vendor.capitalized
  }

  var onlyCursorOtherLimited: Bool {
    guard vendor.lowercased() == "cursor", cooldownUntil.map({ $0 > .now }) != true else { return false }
    let activeCooldowns = (cooldowns ?? [:]).filter { $0.value > .now }
    let limitedWindows = quotaWindows.filter { ($0.usedPercent ?? 0) >= 100 && ($0.resetsAt.map { $0 > .now } ?? true) }
    return (!activeCooldowns.isEmpty || !limitedWindows.isEmpty)
      && activeCooldowns.keys.allSatisfy { $0 == "cursor-other" }
      && limitedWindows.allSatisfy { $0.id == "cursor-other" }
  }

  var displayStatus: String {
    if onlyCursorOtherLimited && enabled && authOk != false && (status == nil || status == "rate_limited") {
      return "active"
    }
    if let status { return status }
    if !enabled { return "paused" }
    if authOk == false { return "reauth_required" }
    if cooldownUntil.map({ $0 > .now }) == true { return "rate_limited" }
    if (cooldowns ?? [:]).values.contains(where: { $0 > .now }) { return "rate_limited" }
    return "active"
  }

  var displayStatusResetAt: Date? {
    if let statusResetAt { return statusResetAt }
    guard displayStatus == "rate_limited" else { return nil }
    let candidates = [cooldownUntil].compactMap { $0 } + Array((cooldowns ?? [:]).values)
    return candidates.filter { $0 > .now }.min()
  }

  var quotaWindows: [SeatQuotaWindow] {
    quota?.windows ?? []
  }

  var primaryQuotaWindow: SeatQuotaWindow? {
    let windows = quotaWindows
    if vendor.lowercased() == "cursor", let models = windows.first(where: { $0.id == "cursor-models" }) {
      return models
    }
    if let id = quota?.primaryWindowId, let match = windows.first(where: { $0.id == id }) {
      return match
    }
    let measured = windows.filter { $0.remainingPercent != nil }
    let available = measured.filter { ($0.remainingPercent ?? 0) > 0 }
    return (available.isEmpty ? measured : available)
      .min { ($0.remainingPercent ?? 100) < ($1.remainingPercent ?? 100) }
  }

  var todayRuns: Int {
    today?.runs ?? lastDay.runs
  }

  var nextQuotaReset: Date? {
    quotaWindows.compactMap(\.resetsAt).min()
  }
}

struct PoolsDocument: Decodable {
  let pools: [PoolEntry]
}

struct PoolRefill: Decodable, Equatable, Sendable {
  let at: Date
  let accounts: Int
  let remainingPercent: Double
}

struct PoolEntry: Decodable, Equatable, Sendable {
  let id: String
  let provider: String
  let kind: String
  let accounts: Int
  let eligibleAccounts: Int
  let status: String
  let windowLabel: String?
  let observedRuns: Int?
  let spentUsd: Double?
  let budgetUsd: Double?
  let monthlyRemainingPercent: Double?
  let unbudgetedAccounts: Int?
  let percentUsed: Double?
  let percentSource: String?
  let cycleResetAt: Date?
  let weeklyRemainingPercent: Double?
  let aggregateRemainingPercent: Double?
  let weeklyResetAt: Date?
  let resetAt: Date?
  let refills: [PoolRefill]?
  let usableAccounts: Int?
  let totalAccounts: Int?
  var headroomPercent: Double? = nil
  var observedTokens: Int? = nil
  var windows: [PoolWindow]? = nil
  var quotaSource: String? = nil
}

struct PoolWindow: Decodable, Equatable, Sendable {
  let id: String
  let kind: String
  let label: String
  var remainingPercent: Double? = nil
  var usedPercent: Double? = nil
  var resetsAt: Date? = nil
  var measuredAccounts: Int? = nil
  var source: String? = nil
}

struct PoolPlan: Decodable, Equatable, Sendable {
  let generatedAt: Date
  let recommended: String
  let levels: [String: PoolPlanLevel]
}

struct PoolPlanLevel: Decodable, Equatable, Sendable {
  let title: String
  let why: String
  let accounts: [PoolPlanAccount]
  let ladders: [String: [PoolPlanRung]]
  let risk: String
}

struct PoolPlanAccount: Decodable, Equatable, Sendable {
  let pool: String
  let label: String
  let count: Int
  let change: String
  let delta: Int
  let reason: String
}

struct PoolPlanRung: Decodable, Equatable, Sendable {
  let id: String
  let model: String
  let harness: String
}
