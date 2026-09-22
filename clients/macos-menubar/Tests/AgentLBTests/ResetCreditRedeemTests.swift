import XCTest
@testable import AgentLB

/// The row offers "reset limits" only where spending a scarce banked credit
/// actually buys capacity back.
final class ResetCreditRedeemTests: XCTestCase {

  private var decoder: JSONDecoder { APIClient.makeDecoder() }

  private func codex(status: String, credits: Int?) -> Account {
    makeTestAccount(provider: "openai", status: status, resetCreditsAvailable: credits)
  }

  // MARK: - Eligibility

  func testQuotaExceededCodexAccountWithCreditCanRedeem() {
    XCTAssertTrue(codex(status: "quota_exceeded", credits: 1).canRedeemResetCredit)
  }

  func testRateLimitedCodexAccountWithCreditCanRedeem() {
    XCTAssertTrue(codex(status: "rate_limited", credits: 2).canRedeemResetCredit)
  }

  func testActiveAccountHasNothingToReset() {
    XCTAssertFalse(codex(status: "active", credits: 2).canRedeemResetCredit)
  }

  func testNoBankedCreditCannotRedeem() {
    XCTAssertFalse(codex(status: "quota_exceeded", credits: 0).canRedeemResetCredit)
  }

  func testUnknownCreditCountCannotRedeem() {
    XCTAssertFalse(codex(status: "quota_exceeded", credits: nil).canRedeemResetCredit)
  }

  func testPausedAccountCannotRedeem() {
    XCTAssertFalse(codex(status: "paused", credits: 2).canRedeemResetCredit)
  }

  func testDisconnectedAccountCannotRedeem() {
    let account = makeTestAccount(
      provider: "openai",
      status: "reauth_required",
      resetCreditsAvailable: 2
    )
    XCTAssertFalse(account.canRedeemResetCredit)
  }

  func testUnsubscribedAccountCannotRedeem() {
    let canceled = AccountSubscriptionLedger(
      status: "canceled",
      amount: nil,
      currency: nil,
      nextChargeAt: nil,
      currentPeriodEndAt: nil,
      lastVerifiedAt: nil
    )
    let account = makeTestAccount(
      provider: "openai",
      status: "quota_exceeded",
      resetCreditsAvailable: 2,
      subscription: canceled
    )
    XCTAssertFalse(account.canRedeemResetCredit)
  }

  /// Banked resets are a Codex feature; Anthropic rows must never offer it.
  func testAnthropicAccountCannotRedeem() {
    let account = makeTestAccount(status: "quota_exceeded", resetCreditsAvailable: 2)
    XCTAssertFalse(account.canRedeemResetCredit)
  }

  // MARK: - Consume response

  func testResetResponseCountsAsApplied() throws {
    let json = """
    {"status": "redeemed", "accountId": "a1", "code": "reset", "windowsReset": 2}
    """.data(using: .utf8)!
    let decoded = try decoder.decode(AccountResetCreditConsumeResponse.self, from: json)
    XCTAssertTrue(decoded.didReset)
    XCTAssertEqual(decoded.windowsReset, 2)
  }

  /// Upstream treats a re-consumed credit as idempotent success.
  func testAlreadyRedeemedCountsAsApplied() throws {
    let json = """
    {"status": "redeemed", "accountId": "a1", "code": "already_redeemed", "windowsReset": 0}
    """.data(using: .utf8)!
    XCTAssertTrue(try decoder.decode(AccountResetCreditConsumeResponse.self, from: json).didReset)
  }

  /// 200 + `nothing_to_reset` is a no-op that keeps the credit banked; the row
  /// must not read it as recovered capacity.
  func testNothingToResetIsNotApplied() throws {
    let json = """
    {"status": "not_redeemed", "accountId": "a1", "code": "nothing_to_reset", "windowsReset": 0}
    """.data(using: .utf8)!
    XCTAssertFalse(try decoder.decode(AccountResetCreditConsumeResponse.self, from: json).didReset)
  }

  func testNoCreditIsNotApplied() throws {
    let json = """
    {"status": "not_redeemed", "accountId": "a1", "code": "no_credit", "windowsReset": 0}
    """.data(using: .utf8)!
    XCTAssertFalse(try decoder.decode(AccountResetCreditConsumeResponse.self, from: json).didReset)
  }
}
