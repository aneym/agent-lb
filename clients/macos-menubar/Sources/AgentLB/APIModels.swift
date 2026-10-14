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
  let accounts: [SeatAccount]
}

struct SeatAccount: Decodable, Equatable, Sendable {
  let id: String
  let vendor: String
  let enabled: Bool
  let authOk: Bool?
  let tier: String?
  let cooldownUntil: Date?
  let cooldowns: [String: Date]?
  let ready: Bool
  let lastDay: SeatUsage
}

struct SeatUsage: Decodable, Equatable, Sendable {
  let runs: Int
  let ok: Int
  let tokensIn: Int
  let tokensOut: Int
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
