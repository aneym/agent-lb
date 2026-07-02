import XCTest
@testable import AgentLB

final class PrivacyMaskTests: XCTestCase {
  func testPerProviderNumberingIsKeyedOnSortedAccountId() {
    let alpha = makeTestAccount(id: "alpha", provider: "anthropic")
    let beta = makeTestAccount(id: "beta", provider: "anthropic")
    let gamma = makeTestAccount(id: "gamma", provider: "openai")
    let zeta = makeTestAccount(id: "zeta", provider: "meta")
    let mask = PrivacyMask.build(enabled: true, accounts: [beta, alpha, gamma, zeta])

    XCTAssertEqual(mask.name(for: alpha, real: "real"), "Claude 1")
    XCTAssertEqual(mask.name(for: beta, real: "real"), "Claude 2")
    XCTAssertEqual(mask.name(for: gamma, real: "real"), "Codex 1")
    XCTAssertEqual(mask.name(for: zeta, real: "real"), "Account 1")
  }

  func testPseudonymsAreStableRegardlessOfInputOrder() {
    let alpha = makeTestAccount(id: "alpha", provider: "anthropic")
    let beta = makeTestAccount(id: "beta", provider: "anthropic")
    let gamma = makeTestAccount(id: "gamma", provider: "openai")
    let zeta = makeTestAccount(id: "zeta", provider: "meta")

    let forward = PrivacyMask.build(enabled: true, accounts: [alpha, beta, gamma, zeta])
    let reversed = PrivacyMask.build(enabled: true, accounts: [zeta, gamma, beta, alpha])

    for account in [alpha, beta, gamma, zeta] {
      XCTAssertEqual(
        forward.name(for: account, real: "real"),
        reversed.name(for: account, real: "real")
      )
    }
  }

  func testAddingANewLaterSortingAccountDoesNotChangeExistingPseudonyms() {
    let alpha = makeTestAccount(id: "alpha", provider: "anthropic")
    let beta = makeTestAccount(id: "beta", provider: "anthropic")
    let before = PrivacyMask.build(enabled: true, accounts: [alpha, beta])

    let zzNew = makeTestAccount(id: "zz-new", provider: "anthropic")
    let after = PrivacyMask.build(enabled: true, accounts: [alpha, beta, zzNew])

    XCTAssertEqual(before.name(for: alpha, real: "real"), after.name(for: alpha, real: "real"))
    XCTAssertEqual(before.name(for: beta, real: "real"), after.name(for: beta, real: "real"))
    XCTAssertEqual(after.name(for: zzNew, real: "real"), "Claude 3")
  }

  func testDisabledMaskIsPassthrough() {
    let account = makeTestAccount(id: "alpha", provider: "anthropic")
    let mask = PrivacyMask.build(enabled: false, accounts: [account])

    XCTAssertEqual(mask, PrivacyMask.disabled)
    XCTAssertEqual(mask.name(for: account, real: "real"), "real")
    XCTAssertEqual(mask.host("m1.local"), "m1.local")
    XCTAssertFalse(mask.enabled)
  }

  func testEnabledMaskRedactsHost() {
    let mask = PrivacyMask.build(enabled: true, accounts: [])
    XCTAssertEqual(mask.host("studio"), "remote")
  }

  func testFallbackLabelsForUnmappedAccountIds() {
    let mask = PrivacyMask.build(enabled: true, accounts: [])

    XCTAssertEqual(
      mask.name(forId: "missing-id", provider: "openai", real: "real@x.com"),
      "Codex"
    )
    XCTAssertEqual(
      mask.name(forId: "missing-id", provider: "anthropic", real: "real@x.com"),
      "Claude"
    )
    XCTAssertEqual(
      mask.name(forId: "missing-id", provider: "unknown-provider", real: "real@x.com"),
      "Account"
    )
  }

  func testNameForIdResolvesKnownIdIgnoringRealString() {
    let alpha = makeTestAccount(id: "alpha", provider: "anthropic")
    let mask = PrivacyMask.build(enabled: true, accounts: [alpha])

    XCTAssertEqual(
      mask.name(forId: "alpha", provider: "anthropic", real: "real@x.com"),
      "Claude 1"
    )
  }
}
