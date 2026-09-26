import XCTest
@testable import AgentLB

final class ProviderScopeTests: XCTestCase {

  // Same fixed "now" as AccountFilterTests: all fixture resetAtPrimary
  // values are in the future relative to 2026-06-10T17:00:00Z.
  private let now = Format.iso8601.date(from: "2026-06-10T17:00:00Z")!

  private var accounts: [Account] = []

  override func setUpWithError() throws {
    accounts = try loadAccountsFixture()
    XCTAssertEqual(accounts.count, 8)
  }

  private func canceledSubscription() -> AccountSubscriptionLedger {
    AccountSubscriptionLedger(
      status: "canceled",
      amount: nil,
      currency: nil,
      nextChargeAt: nil,
      currentPeriodEndAt: nil,
      lastVerifiedAt: nil
    )
  }

  // MARK: - Labels (dashboard providerLabel)

  func testSegmentLabels() {
    XCTAssertEqual(ProviderScope.all.label, "All")
    XCTAssertEqual(ProviderScope.openai.label, "Codex")
    XCTAssertEqual(ProviderScope.anthropic.label, "Claude")
  }

  // MARK: - Scoping

  func testFilterByScope() {
    XCTAssertEqual(ProviderScope.all.filter(accounts).count, 8)
    XCTAssertEqual(ProviderScope.anthropic.filter(accounts).count, 3)
    XCTAssertEqual(ProviderScope.openai.filter(accounts).count, 5)
  }

  func testIncludesIsCaseInsensitive() {
    let upper = makeTestAccount(provider: "OpenAI")
    XCTAssertTrue(ProviderScope.openai.includes(upper))
    XCTAssertFalse(ProviderScope.anthropic.includes(upper))
  }

  // MARK: - §13 providerParam

  func testProviderParam() {
    XCTAssertNil(ProviderScope.all.providerParam)
    XCTAssertEqual(ProviderScope.openai.providerParam, "openai")
    XCTAssertEqual(ProviderScope.anthropic.providerParam, "anthropic")
  }

  func testCounts() {
    let counts = ProviderScope.counts(in: accounts)
    XCTAssertEqual(counts[.all], 8)
    XCTAssertEqual(counts[.anthropic], 3)
    XCTAssertEqual(counts[.openai], 5)
  }

  func testScopeCountsExcludeCanceledSubscription() {
    let activeOne = makeTestAccount(id: "active-1", provider: "anthropic")
    let activeTwo = makeTestAccount(id: "active-2", provider: "anthropic")
    let canceled = makeTestAccount(
      id: "canceled",
      provider: "anthropic",
      subscription: canceledSubscription()
    )

    let counts = ProviderScope.counts(in: [activeOne, canceled, activeTwo])

    XCTAssertEqual(counts[.all], 2)
    XCTAssertEqual(counts[.anthropic], 2)
    XCTAssertEqual(counts[.openai], 0)
  }

  func testScopeCountsExcludeDisconnected() {
    let active = makeTestAccount(id: "active", provider: "anthropic")
    let disconnected = makeTestAccount(
      id: "reauth",
      provider: "anthropic",
      status: "reauth_required",
      deactivationReason: nil
    )

    let counts = ProviderScope.counts(in: [active, disconnected])

    XCTAssertEqual(counts[.all], 1)
    XCTAssertEqual(counts[.anthropic], 1)
  }

  // MARK: - Scoped window math (§9.2: Σ remaining / Σ capacity × 100)

  func testAnthropicPrimaryWindowSums() throws {
    let scoped = ProviderScope.anthropic.filter(accounts)
    let scopedWindow = ProviderScope.summarizeWindow(scoped, window: .primary, now: now)
    let window = scopedWindow.usage

    // remainingCreditsPrimary: 7 + 2 + 3 = 12; capacityCreditsPrimary: 3 × 100
    XCTAssertEqual(try XCTUnwrap(window.remainingCredits), 12.0, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(window.capacityCredits), 300.0, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(window.remainingPercent), 4.0, accuracy: 1e-9)

    // Earliest FUTURE resetAtPrimary among 23:49:59 / 21:50:00 / 23:50:00.
    XCTAssertEqual(window.resetAt, Format.iso8601.date(from: "2026-06-10T21:50:00Z"))

    // §10: only the earliest 1-minute bucket recovers — ddb5ff1a (21:50:00,
    // 100 − 2 = 98). The 23:49:59/23:50:00 resets are staggered later.
    XCTAssertEqual(try XCTUnwrap(scopedWindow.recoveredCredits), 98.0, accuracy: 1e-9)
  }

  func testAnthropicSecondaryWindowSums() throws {
    let scoped = ProviderScope.anthropic.filter(accounts)
    let scopedWindow = ProviderScope.summarizeWindow(scoped, window: .secondary, now: now)
    let window = scopedWindow.usage

    // 41 + 60 + 53 = 154 of 300 → 51.333…%
    XCTAssertEqual(try XCTUnwrap(window.remainingCredits), 154.0, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(window.remainingPercent), 154.0 / 300.0 * 100, accuracy: 1e-9)
    XCTAssertEqual(window.resetAt, Format.iso8601.date(from: "2026-06-14T04:00:00Z"))

    // §10: earliest secondary reset is prove-it (06-14T04:00, 100 − 53 = 47).
    XCTAssertEqual(try XCTUnwrap(scopedWindow.recoveredCredits), 47.0, accuracy: 1e-9)
  }

  func testOpenAIPrimaryWindowSums() throws {
    let scoped = ProviderScope.openai.filter(accounts)
    let scopedWindow = ProviderScope.summarizeWindow(scoped, window: .primary, now: now)
    let window = scopedWindow.usage

    // 1485 + 1470 + 1500 + 1500 + 1500 = 7455 of 7500 → 99.4 %
    XCTAssertEqual(try XCTUnwrap(window.remainingCredits), 7455.0, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(window.capacityCredits), 7500.0, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(window.remainingPercent), 99.4, accuracy: 1e-9)
    XCTAssertEqual(window.resetAt, Format.iso8601.date(from: "2026-06-10T23:00:10Z"))

    // §10: earliest is abreezyish (23:00:10, 1500 − 1470 = 30); the three
    // 02:15:2x resets share a minute but are NOT the earliest bucket.
    XCTAssertEqual(try XCTUnwrap(scopedWindow.recoveredCredits), 30.0, accuracy: 1e-9)
  }

  func testPoolPercentExcludesCanceledCredits() throws {
    let active = makeTestAccount(
      id: "active",
      remainingCreditsPrimary: 50,
      capacityCreditsPrimary: 100,
      resetAtPrimary: now.addingTimeInterval(600)
    )
    let canceled = makeTestAccount(
      id: "canceled",
      remainingCreditsPrimary: 100,
      capacityCreditsPrimary: 100,
      resetAtPrimary: now.addingTimeInterval(300),
      subscription: canceledSubscription()
    )

    let window = ProviderScope.summarizeWindow([active, canceled], window: .primary, now: now)

    XCTAssertEqual(try XCTUnwrap(window.usage.remainingCredits), 50.0, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(window.usage.capacityCredits), 100.0, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(window.usage.remainingPercent), 50.0, accuracy: 1e-9)
    XCTAssertEqual(window.usage.resetAt, now.addingTimeInterval(600))
  }

  func testPoolPercentExcludesPausedCredits() throws {
    let active = makeTestAccount(
      id: "active",
      remainingCreditsPrimary: 50,
      capacityCreditsPrimary: 100,
      resetAtPrimary: now.addingTimeInterval(600)
    )
    let paused = makeTestAccount(
      id: "paused",
      status: "paused",
      remainingCreditsPrimary: 100,
      capacityCreditsPrimary: 100,
      resetAtPrimary: now.addingTimeInterval(300)
    )

    let window = ProviderScope.summarizeWindow([active, paused], window: .primary, now: now)

    XCTAssertEqual(try XCTUnwrap(window.usage.remainingCredits), 50.0, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(window.usage.capacityCredits), 100.0, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(window.usage.remainingPercent), 50.0, accuracy: 1e-9)
    XCTAssertEqual(window.usage.resetAt, now.addingTimeInterval(600))
  }

  // MARK: - §10 recovery edge cases

  func testSameMinuteResetsSumRecovery() throws {
    // Two accounts reset 30 s apart (same 1-minute bucket), a third later.
    let first = makeTestAccount(
      id: "a", remainingCreditsPrimary: 40, capacityCreditsPrimary: 100,
      resetAtPrimary: now.addingTimeInterval(600))
    let second = makeTestAccount(
      id: "b", remainingCreditsPrimary: 70, capacityCreditsPrimary: 100,
      resetAtPrimary: now.addingTimeInterval(630))
    let later = makeTestAccount(
      id: "c", remainingCreditsPrimary: 0, capacityCreditsPrimary: 100,
      resetAtPrimary: now.addingTimeInterval(3600))

    let window = ProviderScope.summarizeWindow([first, second, later], window: .primary, now: now)
    // (100−40) + (100−70) = 90; the later account's 100 does not count.
    XCTAssertEqual(try XCTUnwrap(window.recoveredCredits), 90.0, accuracy: 1e-9)
  }

  func testSingleAccountRecoveryIsCapacityMinusRemaining() throws {
    let solo = makeTestAccount(
      remainingCreditsPrimary: 7, capacityCreditsPrimary: 100,
      resetAtPrimary: now.addingTimeInterval(900))
    let window = ProviderScope.summarizeWindow([solo], window: .primary, now: now)
    XCTAssertEqual(try XCTUnwrap(window.recoveredCredits), 93.0, accuracy: 1e-9)
  }

  func testRecoveryNilWhenBucketLacksCredits() {
    // The earliest-resetting account exposes no credit fields; a credited
    // account resets an hour later (outside the bucket) → unknowable.
    let resetOnly = makeTestAccount(id: "bare", resetAtPrimary: now.addingTimeInterval(600))
    let credited = makeTestAccount(
      id: "full", remainingCreditsPrimary: 10, capacityCreditsPrimary: 100,
      resetAtPrimary: now.addingTimeInterval(4200))
    let window = ProviderScope.summarizeWindow([resetOnly, credited], window: .primary, now: now)
    XCTAssertNil(window.recoveredCredits)
  }

  // MARK: - §10 reset schedule (card tooltip)

  func testResetScheduleSoonestFirstWithRecovery() throws {
    let scoped = ProviderScope.anthropic.filter(accounts)
    let schedule = ProviderScope.resetSchedule(scoped, window: .primary, now: now)

    XCTAssertEqual(schedule.count, 3)
    XCTAssertEqual(schedule[0].displayName, "alex@kineticapps.io")
    XCTAssertEqual(schedule[0].resetAt, Format.iso8601.date(from: "2026-06-10T21:50:00Z"))
    XCTAssertEqual(try XCTUnwrap(schedule[0].recoveredCredits), 98.0, accuracy: 1e-9)

    XCTAssertEqual(schedule[1].displayName, "a.neyman17@gmail.com")
    XCTAssertEqual(try XCTUnwrap(schedule[1].recoveredCredits), 93.0, accuracy: 1e-9)

    XCTAssertEqual(schedule[2].displayName, "alex@prove-it.io")
    XCTAssertEqual(try XCTUnwrap(schedule[2].recoveredCredits), 97.0, accuracy: 1e-9)
  }

  func testResetScheduleSkipsPastResetsAndMarksUnknownRecovery() {
    let past = makeTestAccount(id: "past", resetAtPrimary: now.addingTimeInterval(-60))
    let bare = makeTestAccount(id: "bare", resetAtPrimary: now.addingTimeInterval(120))
    let schedule = ProviderScope.resetSchedule([past, bare], window: .primary, now: now)
    XCTAssertEqual(schedule.count, 1)
    XCTAssertNil(schedule[0].recoveredCredits)
  }

  // MARK: - Edge cases

  func testEmptyScopeRendersNils() {
    let scopedWindow = ProviderScope.summarizeWindow([], window: .primary, now: now)
    XCTAssertNil(scopedWindow.usage.remainingPercent)
    XCTAssertNil(scopedWindow.usage.remainingCredits)
    XCTAssertNil(scopedWindow.usage.capacityCredits)
    XCTAssertNil(scopedWindow.usage.resetAt)
    XCTAssertNil(scopedWindow.recoveredCredits)
  }

  func testZeroCapacityYieldsNilPercent() {
    let zero = makeTestAccount(remainingCreditsPrimary: 0, capacityCreditsPrimary: 0)
    let window = ProviderScope.summarizeWindow([zero], window: .primary, now: now)
    XCTAssertNil(window.usage.remainingPercent)
  }

  func testAccountsMissingCreditsStillContributeResets() throws {
    let withCredits = makeTestAccount(
      id: "credits",
      remainingCreditsPrimary: 10,
      capacityCreditsPrimary: 100,
      resetAtPrimary: now.addingTimeInterval(7200)
    )
    let resetOnly = makeTestAccount(
      id: "reset-only",
      resetAtPrimary: now.addingTimeInterval(3600)
    )
    let scopedWindow = ProviderScope.summarizeWindow(
      [withCredits, resetOnly], window: .primary, now: now
    )
    // Sums come from the credit-bearing account only…
    XCTAssertEqual(try XCTUnwrap(scopedWindow.usage.remainingPercent), 10.0, accuracy: 1e-9)
    XCTAssertEqual(try XCTUnwrap(scopedWindow.usage.capacityCredits), 100.0, accuracy: 1e-9)
    // …but the earliest future reset can come from the other account
    // (whose recovery is then unknowable — §10).
    XCTAssertEqual(scopedWindow.usage.resetAt, now.addingTimeInterval(3600))
    XCTAssertNil(scopedWindow.recoveredCredits)
  }

  func testPastResetsAreIgnored() {
    let stale = makeTestAccount(
      remainingCreditsPrimary: 5,
      capacityCreditsPrimary: 100,
      resetAtPrimary: now.addingTimeInterval(-3600)
    )
    let window = ProviderScope.summarizeWindow([stale], window: .primary, now: now)
    XCTAssertNotNil(window.usage.remainingPercent)
    XCTAssertNil(window.usage.resetAt)
    XCTAssertNil(window.recoveredCredits)
  }
}
