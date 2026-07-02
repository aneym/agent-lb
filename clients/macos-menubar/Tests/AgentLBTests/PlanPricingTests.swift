import XCTest
@testable import AgentLB

final class PlanPricingTests: XCTestCase {

  // MARK: - monthlyUSD(provider:planType:)

  func testMonthlyUSDAnthropicTable() {
    XCTAssertEqual(PlanPricing.monthlyUSD(provider: "anthropic", planType: "claude"), 200)
    XCTAssertEqual(PlanPricing.monthlyUSD(provider: "anthropic", planType: "max"), 200)
    XCTAssertEqual(PlanPricing.monthlyUSD(provider: "anthropic", planType: "max5"), 100)
    XCTAssertEqual(PlanPricing.monthlyUSD(provider: "anthropic", planType: "max_5x"), 100)
    XCTAssertEqual(PlanPricing.monthlyUSD(provider: "anthropic", planType: "pro"), 20)
    XCTAssertEqual(PlanPricing.monthlyUSD(provider: "anthropic", planType: "free"), 0)
    XCTAssertNil(PlanPricing.monthlyUSD(provider: "anthropic", planType: "team"))
    XCTAssertNil(PlanPricing.monthlyUSD(provider: "anthropic", planType: nil))
  }

  func testMonthlyUSDOpenAITable() {
    XCTAssertEqual(PlanPricing.monthlyUSD(provider: "openai", planType: "pro"), 200)
    XCTAssertEqual(PlanPricing.monthlyUSD(provider: "openai", planType: "plus"), 20)
    XCTAssertEqual(PlanPricing.monthlyUSD(provider: "openai", planType: "free"), 0)
    XCTAssertNil(PlanPricing.monthlyUSD(provider: "openai", planType: "business"))
  }

  func testMonthlyUSDUnknownProviderIsNil() {
    XCTAssertNil(PlanPricing.monthlyUSD(provider: "google", planType: "pro"))
  }

  func testMonthlyUSDCaseInsensitive() {
    XCTAssertEqual(PlanPricing.monthlyUSD(provider: "Anthropic", planType: "MAX"), 200)
  }

  // MARK: - monthlyUSD(for:)

  func testMonthlyUSDForAccountUsesOperatorAmount() {
    let account = makeTestAccount(
      subscription: AccountSubscriptionLedger(
        status: "active",
        amount: 150,
        currency: "USD",
        nextChargeAt: nil,
        currentPeriodEndAt: nil,
        lastVerifiedAt: nil
      )
    )
    let result = PlanPricing.monthlyUSD(for: account)
    XCTAssertEqual(result?.amount, 150)
    XCTAssertEqual(result?.estimated, false)
  }

  func testMonthlyUSDForAccountNonUSDCurrencyFallsBackToTable() {
    let account = makeTestAccount(
      planType: "claude",
      subscription: AccountSubscriptionLedger(
        status: "active",
        amount: 150,
        currency: "eur",
        nextChargeAt: nil,
        currentPeriodEndAt: nil,
        lastVerifiedAt: nil
      )
    )
    let result = PlanPricing.monthlyUSD(for: account)
    XCTAssertEqual(result?.amount, 200)
    XCTAssertEqual(result?.estimated, true)
  }

  func testMonthlyUSDForAccountNoAmountFallsBackToTable() {
    let account = makeTestAccount(planType: "claude")
    let result = PlanPricing.monthlyUSD(for: account)
    XCTAssertEqual(result?.amount, 200)
    XCTAssertEqual(result?.estimated, true)
  }

  func testMonthlyUSDForAccountZeroAmountIsIgnored() {
    let account = makeTestAccount(
      planType: "claude",
      subscription: AccountSubscriptionLedger(
        status: "active",
        amount: 0,
        currency: "USD",
        nextChargeAt: nil,
        currentPeriodEndAt: nil,
        lastVerifiedAt: nil
      )
    )
    let result = PlanPricing.monthlyUSD(for: account)
    XCTAssertEqual(result?.amount, 200)
    XCTAssertEqual(result?.estimated, true)
  }

  func testMonthlyUSDForAccountNilCurrencyTreatedAsUSD() {
    let account = makeTestAccount(
      subscription: AccountSubscriptionLedger(
        status: "active",
        amount: 150,
        currency: nil,
        nextChargeAt: nil,
        currentPeriodEndAt: nil,
        lastVerifiedAt: nil
      )
    )
    let result = PlanPricing.monthlyUSD(for: account)
    XCTAssertEqual(result?.amount, 150)
    XCTAssertEqual(result?.estimated, false)
  }

  func testMonthlyUSDForAccountUnpricedPlanIsNil() {
    let account = makeTestAccount(provider: "openai", planType: "team")
    XCTAssertNil(PlanPricing.monthlyUSD(for: account))
  }

  // MARK: - ArbitrageStats.compute

  func testComputeNilSummaryIsNil() {
    XCTAssertNil(ArbitrageStats.compute(summary: nil, accounts: []))
  }

  func testComputeNilTotalUsd7dIsNil() {
    let summary = UsageSummary(
      primaryWindow: nil,
      secondaryWindow: nil,
      monthlyWindow: nil,
      cost: CostSummary(currency: "USD", totalUsd7d: nil),
      metrics: nil
    )
    XCTAssertNil(ArbitrageStats.compute(summary: summary, accounts: []))
  }

  func testComputeZeroTotalUsd7dIsNil() {
    let summary = UsageSummary(
      primaryWindow: nil,
      secondaryWindow: nil,
      monthlyWindow: nil,
      cost: CostSummary(currency: "USD", totalUsd7d: 0),
      metrics: nil
    )
    XCTAssertNil(ArbitrageStats.compute(summary: summary, accounts: []))
  }

  func testComputeAllAccountsUnpricedIsNil() {
    let summary = UsageSummary(
      primaryWindow: nil,
      secondaryWindow: nil,
      monthlyWindow: nil,
      cost: CostSummary(currency: "USD", totalUsd7d: 6200),
      metrics: nil
    )
    let accounts = [
      makeTestAccount(provider: "openai", planType: "team")
    ]
    XCTAssertNil(ArbitrageStats.compute(summary: summary, accounts: accounts))
  }

  func testComputeWorkedExample() {
    let summary = UsageSummary(
      primaryWindow: nil,
      secondaryWindow: nil,
      monthlyWindow: nil,
      cost: CostSummary(currency: "USD", totalUsd7d: 6200),
      metrics: nil
    )
    let accounts = [
      makeTestAccount(id: "a1", provider: "anthropic", planType: "claude"),
      makeTestAccount(id: "a2", provider: "anthropic", planType: "claude"),
      makeTestAccount(
        id: "a3",
        provider: "openai",
        planType: "pro",
        subscription: AccountSubscriptionLedger(
          status: "active",
          amount: 200,
          currency: "USD",
          nextChargeAt: nil,
          currentPeriodEndAt: nil,
          lastVerifiedAt: nil
        )
      ),
      makeTestAccount(
        id: "a4",
        provider: "anthropic",
        planType: "claude",
        subscription: AccountSubscriptionLedger(
          status: "canceled",
          amount: nil,
          currency: nil,
          nextChargeAt: nil,
          currentPeriodEndAt: nil,
          lastVerifiedAt: nil
        )
      ),
      makeTestAccount(id: "a5", provider: "openai", planType: "team"),
    ]
    let stats = ArbitrageStats.compute(summary: summary, accounts: accounts)
    XCTAssertEqual(stats?.monthlyPlanUSD, 600)
    XCTAssertEqual(stats?.planCount, 3)
    XCTAssertEqual(stats?.estimated, true)
    XCTAssertEqual(stats?.valueUSD, 6200)
    XCTAssertEqual(stats?.weeklyPlanUSD ?? 0, 137.98768, accuracy: 0.001)
    XCTAssertEqual(stats?.multiple ?? 0, 44.93, accuracy: 0.01)
  }

  func testComputeEstimatedFalseWhenAllPricesOperatorEntered() {
    let summary = UsageSummary(
      primaryWindow: nil,
      secondaryWindow: nil,
      monthlyWindow: nil,
      cost: CostSummary(currency: "USD", totalUsd7d: 6200),
      metrics: nil
    )
    let accounts = [
      makeTestAccount(
        id: "b1",
        provider: "anthropic",
        subscription: AccountSubscriptionLedger(
          status: "active",
          amount: 200,
          currency: "USD",
          nextChargeAt: nil,
          currentPeriodEndAt: nil,
          lastVerifiedAt: nil
        )
      )
    ]
    let stats = ArbitrageStats.compute(summary: summary, accounts: accounts)
    XCTAssertEqual(stats?.estimated, false)
  }

  // MARK: - multipleLabel

  func testMultipleLabelEstimated() {
    let stats = ArbitrageStats(
      valueUSD: 6200,
      monthlyPlanUSD: 600,
      weeklyPlanUSD: 137.98768,
      planCount: 3,
      multiple: 44.93,
      estimated: true
    )
    XCTAssertEqual(stats.multipleLabel, "≈45×")
  }

  func testMultipleLabelNotEstimated() {
    let stats = ArbitrageStats(
      valueUSD: 100,
      monthlyPlanUSD: 200,
      weeklyPlanUSD: 29.07,
      planCount: 1,
      multiple: 3.44,
      estimated: false
    )
    XCTAssertEqual(stats.multipleLabel, "3.4×")
  }
}
