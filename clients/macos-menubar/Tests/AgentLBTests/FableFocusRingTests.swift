import XCTest

@testable import AgentLB

final class FableFocusRingTests: XCTestCase {
  func testFablePoolPercentAveragesRoutableAnthropicAccounts() {
    let accounts = [
      makeTestAccount(id: "a", additionalQuotas: [makeFableScopedQuota(usedPercent: 20)]),
      makeTestAccount(id: "b", additionalQuotas: [makeFableScopedQuota(usedPercent: 60)]),
    ]
    XCTAssertEqual(AppState.fablePoolPercent(accounts: accounts), 60)
  }

  func testFablePoolPercentKeepsExhaustedRoutableAccountsInDenominator() {
    let accounts = [
      makeTestAccount(
        id: "spent", fableEligible: false,
        additionalQuotas: [makeFableScopedQuota(usedPercent: 100)]
      ),
      makeTestAccount(
        id: "fresh", fableEligible: true,
        additionalQuotas: [makeFableScopedQuota(usedPercent: 0)]
      ),
    ]
    XCTAssertEqual(AppState.fablePoolPercent(accounts: accounts), 50)
  }

  func testFablePoolPercentExcludesNonRoutableAndNonAnthropic() {
    let accounts = [
      makeTestAccount(
        id: "paused", status: "paused",
        additionalQuotas: [makeFableScopedQuota(usedPercent: 0)]
      ),
      makeTestAccount(
        id: "gone",
        additionalQuotas: [makeFableScopedQuota(usedPercent: 0)],
        deactivationReason: "revoked"
      ),
      makeTestAccount(
        id: "openai", provider: "openai",
        additionalQuotas: [makeFableScopedQuota(usedPercent: 0)]
      ),
      makeTestAccount(id: "live", additionalQuotas: [makeFableScopedQuota(usedPercent: 75)]),
    ]
    XCTAssertEqual(AppState.fablePoolPercent(accounts: accounts), 25)
  }

  func testFablePoolPercentNilWithoutScopedData() {
    XCTAssertNil(AppState.fablePoolPercent(accounts: [makeTestAccount(id: "a")]))
    XCTAssertNil(AppState.fablePoolPercent(accounts: []))
  }

  func testClaudeAppMatchingIsAnthropicBundlePrefix() {
    XCTAssertTrue(AppState.isClaudeApp(bundleIdentifier: "com.anthropic.claudefordesktop"))
    XCTAssertTrue(AppState.isClaudeApp(bundleIdentifier: "com.anthropic.claude-code"))
    XCTAssertFalse(AppState.isClaudeApp(bundleIdentifier: "com.apple.Terminal"))
    XCTAssertFalse(AppState.isClaudeApp(bundleIdentifier: "com.anthropic"))
    XCTAssertFalse(AppState.isClaudeApp(bundleIdentifier: nil))
  }

  @MainActor
  func testStatusIconWidensOnlyWhenFableCellShown() {
    let base = StatusIconRenderer.icon(for: .healthy, primaryPercent: 50, longWindowPercent: 50)
    let fable = StatusIconRenderer.icon(
      for: .healthy, primaryPercent: 50, longWindowPercent: 50,
      showFable: true, fablePercent: 62
    )
    XCTAssertEqual(base.size.width, 18)
    XCTAssertEqual(base.size.height, 18)
    XCTAssertEqual(fable.size.width, 38)
    XCTAssertEqual(fable.size.height, 18)
  }
}
