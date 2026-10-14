import XCTest
@testable import AgentLB



final class ResetCreditRedeemTests: XCTestCase {

  private var decoder: JSONDecoder { APIClient.makeDecoder() }

  private func codex(status: String, credits: Int?) -> Account {
    makeTestAccount(provider: "openai", status: status, resetCreditsAvailable: credits)
  }



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

  func testQuotaExceededClaudeAccountWithBankedResetCanRedeem() {
    let account = makeTestAccount(
      provider: "anthropic",
      status: "quota_exceeded",
      resetCreditsAvailable: 1
    )
    XCTAssertTrue(account.canRedeemResetCredit)
  }

  func testProviderWithoutBankedResetsCannotRedeem() {
    let account = makeTestAccount(provider: "glm", status: "quota_exceeded", resetCreditsAvailable: 1)
    XCTAssertFalse(account.canRedeemResetCredit)
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

  func testAnthropicLimitBlockedAccountWithCreditCanRedeem() {
    let account = makeTestAccount(
      provider: "anthropic",
      status: "quota_exceeded",
      resetCreditsAvailable: 2
    )
    XCTAssertTrue(account.canRedeemResetCredit)
  }



  func testResetResponseCountsAsApplied() throws {
    let json = """
    {"status": "redeemed", "accountId": "a1", "code": "reset", "windowsReset": 2}
    """.data(using: .utf8)!
    let decoded = try decoder.decode(AccountResetCreditConsumeResponse.self, from: json)
    XCTAssertTrue(decoded.didReset)
    XCTAssertEqual(decoded.windowsReset, 2)
  }


  func testAlreadyRedeemedCountsAsApplied() throws {
    let json = """
    {"status": "redeemed", "accountId": "a1", "code": "already_redeemed", "windowsReset": 0}
    """.data(using: .utf8)!
    XCTAssertTrue(try decoder.decode(AccountResetCreditConsumeResponse.self, from: json).didReset)
  }



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
