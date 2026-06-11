import Foundation

// MARK: - Accounts

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
  let usage: AccountUsage
  // §9.2: per-account credit sums drive the scoped pool windows.
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

  var id: String { accountId }
}

struct AccountUsage: Decodable, Sendable, Equatable {
  let primaryRemainingPercent: Double?
  let secondaryRemainingPercent: Double?
  let monthlyRemainingPercent: Double?
}

// MARK: - Usage Summary

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

// MARK: - Projections

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

// MARK: - Request Logs

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

// MARK: - Runtime Version

struct RuntimeVersion: Decodable, Sendable, Equatable {
  let currentVersion: String?
  let latestVersion: String?
  let updateAvailable: Bool
  let releaseUrl: String?
}

// MARK: - Health

struct HealthResponse: Decodable, Sendable {
  let status: String
}
