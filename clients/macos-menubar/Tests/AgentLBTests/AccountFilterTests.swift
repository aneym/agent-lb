import XCTest
@testable import AgentLB

final class AccountFilterTests: XCTestCase {

  // Fixed "now" between the fixture's past rateLimitResetAt
  // (2026-06-05T22:17:58Z, openai alex@prove-it.io) and the future one
  // (2026-06-10T18:20:00Z, anthropic a.neyman17@gmail.com).
  private let now = Format.iso8601.date(from: "2026-06-10T17:00:00Z")!

  private var accounts: [Account] = []

  override func setUpWithError() throws {
    accounts = try loadAccountsFixture()
    XCTAssertEqual(accounts.count, 8)
  }

  private func makeAccount(
    id: String = "synthetic",
    provider: String = "anthropic",
    displayName: String = "synthetic@example.com",
    status: String = "active",
    resetAtPrimary: Date? = nil,
    resetAtSecondary: Date? = nil,
    rateLimitResetAt: Date? = nil,
    deactivationReason: String? = nil,
    subscription: AccountSubscriptionLedger? = nil
  ) -> Account {
    makeTestAccount(
      id: id,
      provider: provider,
      displayName: displayName,
      status: status,
      resetAtPrimary: resetAtPrimary,
      resetAtSecondary: resetAtSecondary,
      rateLimitResetAt: rateLimitResetAt,
      deactivationReason: deactivationReason,
      subscription: subscription
    )
  }

  private func canceledSubscription() -> AccountSubscriptionLedger {
    AccountSubscriptionLedger(
      status: "canceled",
      nextChargeAt: nil,
      currentPeriodEndAt: nil,
      lastVerifiedAt: nil
    )
  }

  // MARK: - Provider filter

  func testProviderFilterAll() {
    let filter = AccountFilter()
    XCTAssertEqual(filter.apply(to: accounts, now: now).count, 8)
  }

  func testProviderFilterAnthropic() {
    let filter = AccountFilter(provider: .anthropic)
    let result = filter.apply(to: accounts, now: now)
    XCTAssertEqual(result.count, 3)
    XCTAssertTrue(result.allSatisfy { $0.provider == "anthropic" })
  }

  func testProviderFilterOpenAI() {
    let filter = AccountFilter(provider: .openai)
    XCTAssertEqual(filter.apply(to: accounts, now: now).count, 5)
  }

  func testProviderMatchingIsCaseInsensitive() {
    let upper = makeAccount(provider: "Anthropic")
    let filter = AccountFilter(provider: .anthropic)
    XCTAssertEqual(filter.apply(to: [upper], now: now).count, 1)
  }

  func testCanceledAccountVisibleInList() {
    let active = makeAccount(id: "active", displayName: "active@example.com")
    let canceled = makeAccount(
      id: "canceled",
      displayName: "canceled@example.com",
      subscription: canceledSubscription()
    )
    let subscriptionPaused = makeAccount(
      id: "subscription-paused",
      displayName: "subscription-paused@example.com",
      subscription: AccountSubscriptionLedger(
        status: "paused",
        nextChargeAt: nil,
        currentPeriodEndAt: nil,
        lastVerifiedAt: nil
      )
    )

    let result = AccountFilter().apply(to: [canceled, active, subscriptionPaused], now: now)

    XCTAssertEqual(Set(result.map(\.accountId)), ["active", "canceled", "subscription-paused"])
    XCTAssertEqual(AccountFilter.classify(canceled, now: now), .unsubscribed)
  }

  // MARK: - Status filter

  func testFutureResetMetadataDoesNotMakeActiveAccountRateLimited() {
    let filter = AccountFilter(status: .rateLimited)
    XCTAssertTrue(filter.apply(to: accounts, now: now).isEmpty)

    let activeWithFutureReset = makeAccount(
      id: "active-reset",
      rateLimitResetAt: now.addingTimeInterval(600)
    )
    XCTAssertEqual(AccountFilter.classify(activeWithFutureReset, now: now), .active)
  }

  func testBlockedBackendStatusCountsAsRateLimited() {
    let blocked = makeAccount(
      id: "blocked",
      status: "rate_limited",
      rateLimitResetAt: now.addingTimeInterval(600)
    )
    let filter = AccountFilter(status: .rateLimited)
    XCTAssertEqual(filter.apply(to: accounts + [blocked], now: now).map(\.accountId), ["blocked"])
    XCTAssertEqual(AccountFilter.classify(blocked, now: now), .rateLimited)
  }

  func testQuotaExceededBackendStatusCountsAsRateLimited() {
    let blocked = makeAccount(id: "quota", status: "quota_exceeded")
    XCTAssertEqual(AccountFilter.classify(blocked, now: now), .rateLimited)
  }

  func testActiveExcludesRateLimited() {
    let blocked = makeAccount(id: "blocked", status: "rate_limited")
    let filter = AccountFilter(status: .active)
    XCTAssertEqual(filter.apply(to: accounts + [blocked], now: now).count, 8)
  }

  func testPausedAndInactiveClassification() {
    let paused = makeAccount(id: "p", status: "paused")
    let deactivated = makeAccount(id: "d", status: "deactivated")
    let reasoned = makeAccount(id: "r", status: "active", deactivationReason: "re-auth required")
    let pool = accounts + [paused, deactivated, reasoned]

    XCTAssertEqual(
      AccountFilter(status: .paused).apply(to: pool, now: now).map(\.accountId), ["p"]
    )
    XCTAssertEqual(
      Set(AccountFilter(status: .inactive).apply(to: pool, now: now).map(\.accountId)),
      ["d", "r"]
    )
  }

  func testReauthRequiredWithoutReasonClassifiedDisconnected() {
    let reauthRequired = makeAccount(
      id: "reauth",
      status: "reauth_required",
      deactivationReason: nil
    )

    XCTAssertTrue(reauthRequired.isDisconnected)
    XCTAssertFalse(reauthRequired.isHeadlineCountable)
    XCTAssertEqual(AccountFilter.classify(reauthRequired, now: now), .inactive)
    XCTAssertTrue(AccountFilter(status: .active).apply(to: [reauthRequired], now: now).isEmpty)
  }

  func testPausedBeatsFutureResetMetadata() {
    let both = makeAccount(
      id: "both",
      status: "paused",
      rateLimitResetAt: now.addingTimeInterval(600)
    )
    XCTAssertEqual(AccountFilter.classify(both, now: now), .paused)
  }

  func testUnknownStatusWithNilSubscriptionStaysActiveAndRoutable() {
    let account = makeAccount(id: "unknown", status: "mystery")

    XCTAssertFalse(account.isSubscriptionCanceled)
    XCTAssertFalse(account.isDisconnected)
    XCTAssertTrue(account.isHeadlineCountable)
    XCTAssertTrue(account.isRoutable)
    XCTAssertEqual(AccountFilter.classify(account, now: now), .active)
  }

  // MARK: - Query filter

  func testQueryMatchesSubstringCaseInsensitively() {
    let filter = AccountFilter(query: "KINETIC")
    let result = filter.apply(to: accounts, now: now)
    XCTAssertEqual(result.count, 2)
    XCTAssertTrue(result.allSatisfy { $0.displayName == "alex@kineticapps.io" })
  }

  func testQueryMatchesAlias() {
    let aliased = makeTestAccount(
      id: "aliased",
      provider: "openai",
      displayName: "x@example.com",
      alias: "workhorse"
    )
    let filter = AccountFilter(query: "horse")
    XCTAssertEqual(filter.apply(to: accounts + [aliased], now: now).map(\.accountId), ["aliased"])
  }

  func testWhitespaceOnlyQueryMatchesEverything() {
    let filter = AccountFilter(query: "   ")
    XCTAssertEqual(filter.apply(to: accounts, now: now).count, 8)
    XCTAssertFalse(filter.hasActiveFilters)
  }

  // MARK: - Sorting

  // Earliest future reset per fixture account at now = 17:00Z:
  //   ddb5ff1a… 21:50:00 < abreezyish 23:00:10 < 2c436b54… 23:49:59
  //   < prove-it/anthropic 23:50:00 < openai a.neyman17 23:55:02
  //   < prove-it/openai 00:29:22+1d < kinetic/openai 02:15:22+1d
  //   < yahoo 02:15:29+1d
  func testSortResetSoonest() {
    let filter = AccountFilter(sort: .resetSoonest)
    let result = filter.apply(to: accounts, now: now)
    XCTAssertEqual(result.first?.accountId, "ddb5ff1a-4aea-4810-9f10-196fb49b5d80")
    XCTAssertEqual(result.last?.accountId, "d69ad7e8-8f62-4559-a742-8ba88e6308a9_73ce034e")
  }

  func testSortResetLatest() {
    let filter = AccountFilter(sort: .resetLatest)
    let result = filter.apply(to: accounts, now: now)
    XCTAssertEqual(result.first?.accountId, "d69ad7e8-8f62-4559-a742-8ba88e6308a9_73ce034e")
    XCTAssertEqual(result.last?.accountId, "ddb5ff1a-4aea-4810-9f10-196fb49b5d80")
  }

  func testNilResetsSortLastInBothDirections() {
    let noReset = makeAccount(id: "no-reset", displayName: "aaaa@example.com")
    for sort in [AccountFilter.Sort.resetSoonest, .resetLatest] {
      let result = AccountFilter(sort: sort).apply(to: accounts + [noReset], now: now)
      XCTAssertEqual(result.last?.accountId, "no-reset", "nil reset must sort last (\(sort))")
    }
  }

  func testPastResetsCountAsNil() {
    let pastOnly = makeAccount(
      id: "past",
      resetAtPrimary: now.addingTimeInterval(-3600),
      resetAtSecondary: now.addingTimeInterval(-60)
    )
    XCTAssertNil(AccountFilter.nextReset(of: pastOnly, now: now))
  }

  func testNextResetPicksEarliestFutureWindow() {
    let account = makeAccount(
      id: "windows",
      resetAtPrimary: now.addingTimeInterval(7200),
      resetAtSecondary: now.addingTimeInterval(3600)
    )
    XCTAssertEqual(AccountFilter.nextReset(of: account, now: now), now.addingTimeInterval(3600))
  }

  func testSortNameAscAndDesc() {
    let asc = AccountFilter(sort: .nameAsc).apply(to: accounts, now: now)
    XCTAssertEqual(asc.first?.displayName, "a.neyman17@gmail.com")
    XCTAssertEqual(asc.last?.displayName, "aneym1@yahoo.com")

    let desc = AccountFilter(sort: .nameDesc).apply(to: accounts, now: now)
    XCTAssertEqual(desc.first?.displayName, "aneym1@yahoo.com")
    XCTAssertEqual(desc.last?.displayName, "a.neyman17@gmail.com")
  }

  // MARK: - Provider counts

  func testProviderCountsUnfiltered() {
    let counts = AccountFilter().providerCounts(in: accounts, now: now)
    XCTAssertEqual(counts[.all], 8)
    XCTAssertEqual(counts[.anthropic], 3)
    XCTAssertEqual(counts[.openai], 5)
  }

  func testProviderCountsApplyStatusAndQueryButNotProvider() {
    // Counts must reflect the other active filters (query) while ignoring
    // the provider selection itself — chips preview what switching yields.
    let filter = AccountFilter(provider: .anthropic, query: "kinetic")
    let counts = filter.providerCounts(in: accounts, now: now)
    XCTAssertEqual(counts[.all], 2)
    XCTAssertEqual(counts[.anthropic], 1)
    XCTAssertEqual(counts[.openai], 1)
  }

  func testProviderCountsWithStatusFilter() {
    let blocked = makeAccount(id: "blocked", provider: "anthropic", status: "rate_limited")
    let filter = AccountFilter(status: .rateLimited)
    let counts = filter.providerCounts(in: accounts + [blocked], now: now)
    XCTAssertEqual(counts[.all], 1)
    XCTAssertEqual(counts[.anthropic], 1)
    XCTAssertEqual(counts[.openai], 0)
  }

  func testProviderCountsIncludeCanceledSubscriptionsByDefault() {
    let canceled = makeAccount(
      id: "canceled",
      provider: "anthropic",
      subscription: canceledSubscription()
    )
    let active = makeAccount(id: "active", provider: "openai")

    let counts = AccountFilter().providerCounts(in: [canceled, active], now: now)

    XCTAssertEqual(counts[.all], 2)
    XCTAssertEqual(counts[.anthropic], 1)
    XCTAssertEqual(counts[.openai], 1)
  }

  // MARK: - hasActiveFilters

  func testHasActiveFilters() {
    XCTAssertFalse(AccountFilter().hasActiveFilters)
    XCTAssertFalse(AccountFilter(sort: .nameDesc).hasActiveFilters, "sort alone is not a filter")
    XCTAssertTrue(AccountFilter(provider: .openai).hasActiveFilters)
    XCTAssertTrue(AccountFilter(status: .paused).hasActiveFilters)
    XCTAssertTrue(AccountFilter(query: "x").hasActiveFilters)
  }
}
