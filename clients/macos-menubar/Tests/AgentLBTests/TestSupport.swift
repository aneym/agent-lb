import Foundation
@testable import AgentLB

/// Shared synthetic-account factory for filter/scope tests.
func makeTestAccount(
  id: String = "synthetic",
  provider: String = "anthropic",
  displayName: String = "synthetic@example.com",
  alias: String? = nil,
  planType: String? = nil,
  status: String = "active",
  remainingCreditsPrimary: Double? = nil,
  capacityCreditsPrimary: Double? = nil,
  remainingCreditsSecondary: Double? = nil,
  capacityCreditsSecondary: Double? = nil,
  resetAtPrimary: Date? = nil,
  resetAtSecondary: Date? = nil,
  rateLimitResetAt: Date? = nil,
  deactivationReason: String? = nil,
  subscription: AccountSubscriptionLedger? = nil
) -> Account {
  Account(
    accountId: id,
    provider: provider,
    email: displayName,
    alias: alias,
    displayName: displayName,
    workspaceLabel: nil,
    planType: planType,
    routingPolicy: nil,
    status: status,
    usage: AccountUsage(
      primaryRemainingPercent: nil,
      secondaryRemainingPercent: nil,
      monthlyRemainingPercent: nil
    ),
    remainingCreditsPrimary: remainingCreditsPrimary,
    capacityCreditsPrimary: capacityCreditsPrimary,
    remainingCreditsSecondary: remainingCreditsSecondary,
    capacityCreditsSecondary: capacityCreditsSecondary,
    resetAtPrimary: resetAtPrimary,
    resetAtSecondary: resetAtSecondary,
    resetAtMonthly: nil,
    rateLimitResetAt: rateLimitResetAt,
    lastRefreshAt: nil,
    deactivationReason: deactivationReason,
    isEmailDuplicate: nil,
    subscription: subscription
  )
}

/// Decodes the shared accounts fixture with the production decoder.
func loadAccountsFixture() throws -> [Account] {
  guard
    let url = Bundle.module.url(
      forResource: "accounts",
      withExtension: "json",
      subdirectory: "Fixtures"
    )
  else {
    throw NSError(
      domain: "AgentLBTests",
      code: 1,
      userInfo: [NSLocalizedDescriptionKey: "Missing fixture: accounts.json"]
    )
  }
  let data = try Data(contentsOf: url)
  return try APIClient.makeDecoder().decode(AccountsResponse.self, from: data).accounts
}
