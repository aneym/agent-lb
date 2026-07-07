import XCTest
@testable import AgentLB

final class ModelDecodingTests: XCTestCase {

  // MARK: - Helpers

  private func fixture(_ filename: String) throws -> Data {
    let url = try XCTUnwrap(
      Bundle.module.url(
        forResource: filename,
        withExtension: nil,
        subdirectory: "Fixtures"
      ),
      "Missing fixture: \(filename)"
    )
    return try Data(contentsOf: url)
  }

  private var decoder: JSONDecoder { APIClient.makeDecoder() }

  // MARK: - accounts.json

  func testAccountsDecoding() throws {
    let data = try fixture("accounts.json")
    let response = try decoder.decode(AccountsResponse.self, from: data)

    // First account asserts
    let first = try XCTUnwrap(response.accounts.first)
    XCTAssertEqual(first.accountId, "2c436b54-a7e2-4299-9d6b-689ad2dda8cb")
    XCTAssertEqual(first.usage.primaryRemainingPercent, 7.0)

    // rateLimitResetAt is whole-second ISO — must parse
    XCTAssertNotNil(first.rateLimitResetAt,
                    "rateLimitResetAt (whole-second ISO-8601) must decode")

    // lastRefreshAt is fractional ISO — must parse
    XCTAssertNotNil(first.lastRefreshAt,
                    "lastRefreshAt (fractional ISO-8601) must decode")

    // Nulls decode as nil
    XCTAssertNil(first.alias)
    XCTAssertNil(first.workspaceLabel)

    // isEmailDuplicate decodes as false (not nil, not true)
    XCTAssertEqual(first.isEmailDuplicate, false)
    XCTAssertEqual(first.fableEligible, true)
    XCTAssertEqual(first.fableAvailability, .available)

    // §9.2: per-account credit fields drive the scoped pool windows
    XCTAssertEqual(first.remainingCreditsPrimary, 7.0)
    XCTAssertEqual(first.capacityCreditsPrimary, 100.0)
    XCTAssertEqual(first.remainingCreditsSecondary, 41.0)
    XCTAssertEqual(first.capacityCreditsSecondary, 100.0)

    // Unknown keys (additionalQuotas, requestUsage) are ignored without error
    XCTAssertEqual(response.accounts.count, 8)
    XCTAssertNil(response.accounts[1].fableEligible)
    XCTAssertNil(response.accounts[1].fableAvailability)
    XCTAssertEqual(
      response.accounts.first { $0.accountId == "ddb5ff1a-4aea-4810-9f10-196fb49b5d80" }?.fableAvailability,
      .out
    )
  }

  func testFableAvailabilityOnlyAppliesToAnthropicAccounts() throws {
    let anthropicOut = makeTestAccount(
      id: "claude-out",
      provider: "anthropic",
      fableEligible: false
    )
    let anthropicAvailable = makeTestAccount(
      id: "claude-ok",
      provider: "Anthropic",
      fableEligible: true
    )
    let openAIWithUnexpectedFlag = makeTestAccount(
      id: "codex",
      provider: "openai",
      fableEligible: false
    )

    XCTAssertEqual(anthropicOut.fableAvailability, .out)
    XCTAssertEqual(anthropicAvailable.fableAvailability, .available)
    XCTAssertNil(openAIWithUnexpectedFlag.fableAvailability)
  }

  func testAccountSubscriptionLedgerDecoding() throws {
    let json = """
    {
      "accounts": [{
        "accountId": "sub-ledger",
        "provider": "anthropic",
        "email": "sub@example.com",
        "displayName": "sub@example.com",
        "status": "active",
        "usage": {
          "primaryRemainingPercent": 100,
          "secondaryRemainingPercent": 99,
          "monthlyRemainingPercent": null
        },
        "remainingCreditsPrimary": null,
        "capacityCreditsPrimary": null,
        "remainingCreditsSecondary": null,
        "capacityCreditsSecondary": null,
        "resetAtPrimary": null,
        "resetAtSecondary": null,
        "resetAtMonthly": null,
        "rateLimitResetAt": null,
        "lastRefreshAt": null,
        "deactivationReason": null,
        "isEmailDuplicate": false,
        "subscription": {
          "status": "active",
          "nextChargeAt": null,
          "currentPeriodEndAt": "2026-06-22T00:00:00Z",
          "lastVerifiedAt": "2026-06-13T15:30:00Z",
          "amount": 20.00,
          "currency": "USD",
          "notes": "operator reported cancellation"
        }
      }]
    }
    """.data(using: .utf8)!

    let response = try decoder.decode(AccountsResponse.self, from: json)
    let account = try XCTUnwrap(response.accounts.first)
    XCTAssertEqual(account.subscription?.status, "active")
    XCTAssertNotNil(account.subscription?.currentPeriodEndAt)
    XCTAssertNotNil(account.subscription?.lastVerifiedAt)
    // §11: the operator-entered monthly price feeds the arbitrage denominator.
    XCTAssertEqual(account.subscription?.amount, 20.00)
    XCTAssertEqual(account.subscription?.currency, "USD")
  }

  // MARK: - summary.json

  func testSummaryDecoding() throws {
    let data = try fixture("summary.json")
    let summary = try decoder.decode(UsageSummary.self, from: data)

    XCTAssertEqual(try XCTUnwrap(summary.cost?.totalUsd7d), 6145.009936, accuracy: 1e-6)
    XCTAssertEqual(summary.metrics?.requests7d, 60899)
    XCTAssertNil(summary.monthlyWindow)
    XCTAssertNotNil(summary.primaryWindow)
    XCTAssertNotNil(summary.secondaryWindow)

    // §8.2 metrics strip line 2 inputs
    XCTAssertEqual(summary.metrics?.tokensSecondaryWindow, 3_834_168_753)
    XCTAssertEqual(summary.metrics?.cachedTokensSecondaryWindow, 3_050_279_380)
  }

  // MARK: - projections.json

  func testProjectionsDecoding() throws {
    let data = try fixture("projections.json")
    let proj = try decoder.decode(ProjectionsResponse.self, from: data)

    XCTAssertEqual(proj.depletionPrimary?.riskLevel, "critical")
    XCTAssertEqual(proj.weeklyCreditPace?.status, "behind")
    XCTAssertNotNil(proj.depletionSecondary)
  }

  // MARK: - request-logs.json

  func testRequestLogsDecoding() throws {
    let data = try fixture("request-logs.json")
    let logs = try decoder.decode(RequestLogsResponse.self, from: data)

    XCTAssertEqual(logs.requests.count, 5)
    // total is Int64-range safe (143334 in fixture)
    XCTAssertEqual(logs.total, 143334)
    XCTAssertTrue(logs.hasMore)

    // costUsd is null in all fixture entries
    XCTAssertNil(logs.requests[0].costUsd)
  }

  func testErrorShapedLogEntry() throws {
    // Craft a minimal error-shaped entry to verify errorCode decodes correctly
    let json = """
    {
      "requests": [{
        "requestedAt": "2026-06-10T21:15:38.008259Z",
        "requestId": "err-test-id",
        "status": "error",
        "errorCode": "rate_limit_exceeded",
        "tokens": null,
        "costUsd": null,
        "latencyMs": 500
      }],
      "total": 1,
      "hasMore": false
    }
    """.data(using: .utf8)!

    let logs = try decoder.decode(RequestLogsResponse.self, from: json)
    let entry = try XCTUnwrap(logs.requests.first)
    XCTAssertEqual(entry.errorCode, "rate_limit_exceeded")
    XCTAssertNil(entry.costUsd)
    XCTAssertNil(entry.accountId)
  }

  // MARK: - version.json

  func testVersionDecoding() throws {
    let data = try fixture("version.json")
    let version = try decoder.decode(RuntimeVersion.self, from: data)

    XCTAssertEqual(version.updateAvailable, false)
    XCTAssertNil(version.latestVersion)
    XCTAssertEqual(version.currentVersion, "1.20.0-beta.3")
  }

  // MARK: - Negative: malformed date

  func testInvalidDateThrows() throws {
    let json = """
    {
      "requestedAt": "not-a-date",
      "requestId": "bad-id",
      "status": "ok"
    }
    """.data(using: .utf8)!

    XCTAssertThrowsError(try decoder.decode(RequestLogEntry.self, from: json)) { error in
      // Must be a DecodingError (APIError.decoding wraps it outside this scope;
      // bare decoder.decode throws DecodingError directly)
      XCTAssertTrue(error is DecodingError, "Expected DecodingError, got \(error)")
    }
  }
}
