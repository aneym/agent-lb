import XCTest
@testable import AgentLB

final class AccountRefreshTests: XCTestCase {

  private var decoder: JSONDecoder { APIClient.makeDecoder() }

  private func canceledSubscription() -> AccountSubscriptionLedger {
    AccountSubscriptionLedger(
      status: "canceled",
      nextChargeAt: nil,
      currentPeriodEndAt: nil,
      lastVerifiedAt: nil
    )
  }

  // MARK: - Endpoint choice

  func testCanceledSubscriptionChecksSubscription() {
    let account = makeTestAccount(status: "active", subscription: canceledSubscription())
    XCTAssertEqual(AccountRefreshAction.action(for: account), .checkSubscription)
  }

  /// Mirrors the row's presentation priority: a canceled ledger wins over
  /// paused, so the control re-verifies the subscription (probe would 409).
  func testCanceledSubscriptionWinsOverPaused() {
    let account = makeTestAccount(status: "paused", subscription: canceledSubscription())
    XCTAssertEqual(AccountRefreshAction.action(for: account), .checkSubscription)
  }

  func testActiveAccountProbes() {
    XCTAssertEqual(AccountRefreshAction.action(for: makeTestAccount(status: "active")), .probe)
  }

  func testActiveSubscriptionLedgerStillProbes() {
    let active = AccountSubscriptionLedger(
      status: "active",
      nextChargeAt: nil,
      currentPeriodEndAt: nil,
      lastVerifiedAt: nil
    )
    let account = makeTestAccount(status: "active", subscription: active)
    XCTAssertEqual(AccountRefreshAction.action(for: account), .probe)
  }

  func testRateLimitedAccountProbes() {
    XCTAssertEqual(
      AccountRefreshAction.action(for: makeTestAccount(status: "rate_limited")),
      .probe
    )
  }

  // Paused/disconnected rows must not surface the control: the server
  // deterministically rejects their probes with 409 account_not_probable.
  func testPausedAccountHasNoAction() {
    XCTAssertNil(AccountRefreshAction.action(for: makeTestAccount(status: "paused")))
  }

  func testDisconnectedAccountsHaveNoAction() {
    XCTAssertNil(AccountRefreshAction.action(for: makeTestAccount(status: "deactivated")))
    XCTAssertNil(AccountRefreshAction.action(for: makeTestAccount(status: "reauth_required")))
    XCTAssertNil(
      AccountRefreshAction.action(
        for: makeTestAccount(status: "active", deactivationReason: "token invalidated")
      )
    )
  }

  // MARK: - Response decoding

  func testProbeResponseDecoding() throws {
    // Full server shape — extra fields (usage before/after, status
    // before/after) must be ignored without error.
    let json = """
    {
      "status": "probed",
      "accountId": "2c436b54-a7e2-4299-9d6b-689ad2dda8cb",
      "probeStatusCode": 200,
      "primaryUsedPercentBefore": 93.0,
      "primaryUsedPercentAfter": 93.5,
      "secondaryUsedPercentBefore": 59.0,
      "secondaryUsedPercentAfter": 59.0,
      "accountStatusBefore": "rate_limited",
      "accountStatusAfter": "active"
    }
    """.data(using: .utf8)!

    let response = try decoder.decode(AccountProbeResponse.self, from: json)
    XCTAssertEqual(response.status, "probed")
    XCTAssertEqual(response.accountId, "2c436b54-a7e2-4299-9d6b-689ad2dda8cb")
    XCTAssertEqual(response.probeStatusCode, 200)
  }

  func testSubscriptionCheckResponseDecoding() throws {
    let json = """
    {
      "status": "checked",
      "accountId": "sub-check",
      "working": true,
      "probeStatusCode": 200,
      "subscription": {
        "status": "active",
        "nextChargeAt": null,
        "currentPeriodEndAt": "2026-07-22T00:00:00Z",
        "lastVerifiedAt": "2026-07-01T15:30:00.123456Z",
        "notes": "auto-verified working (probe 200)"
      },
      "message": null
    }
    """.data(using: .utf8)!

    let response = try decoder.decode(AccountSubscriptionCheckResponse.self, from: json)
    XCTAssertEqual(response.status, "checked")
    XCTAssertTrue(response.working)
    XCTAssertEqual(response.probeStatusCode, 200)
    XCTAssertEqual(response.subscription?.status, "active")
    XCTAssertNotNil(response.subscription?.lastVerifiedAt)
  }

  func testSubscriptionCheckResponseStillCanceledDecoding() throws {
    let json = """
    {
      "status": "checked",
      "accountId": "sub-check",
      "working": false,
      "probeStatusCode": 403,
      "subscription": null,
      "message": "subscription required"
    }
    """.data(using: .utf8)!

    let response = try decoder.decode(AccountSubscriptionCheckResponse.self, from: json)
    XCTAssertFalse(response.working)
    XCTAssertEqual(response.probeStatusCode, 403)
    XCTAssertNil(response.subscription)
  }
}
