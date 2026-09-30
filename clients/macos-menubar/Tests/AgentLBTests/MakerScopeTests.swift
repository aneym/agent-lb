import XCTest
@testable import AgentLB

/// Routing scope 2026-09-30: All counts every account the router can use, LB and seat, and the
/// Cursor and Devin scopes show their pools and accounts.
final class MakerScopeTests: XCTestCase {
  private let seatsJSON = """
  {"stateUpdatedAt":"2026-09-30T02:40:00Z","source":"seat_state","accounts":[
   {"id":"cursor-main","vendor":"cursor","enabled":true,"authOk":true,"tier":"Pro+","ready":true,
    "lastDay":{"runs":3,"ok":3,"wallS":90,"tokensIn":1000,"tokensOut":100}},
   {"id":"cursor-gmail","vendor":"cursor","enabled":true,"authOk":true,"tier":null,"ready":true,
    "lastDay":{"runs":0,"ok":0,"wallS":0,"tokensIn":0,"tokensOut":0}},
   {"id":"devin-main","vendor":"devin","enabled":true,"authOk":true,"tier":null,"ready":true,
    "lastDay":{"runs":2,"ok":2,"wallS":60,"tokensIn":0,"tokensOut":0}},
   {"id":"devin-kinetic","vendor":"devin","enabled":false,"authOk":null,"tier":null,"ready":false,
    "lastDay":{"runs":0,"ok":0,"wallS":0,"tokensIn":0,"tokensOut":0}}]}
  """
  private let poolsJSON = """
  {"generatedAt":"2026-09-30T02:45:00Z","pools":[
   {"id":"openai-codex","provider":"openai","kind":"weekly","accounts":5,"eligibleAccounts":4,"status":"ok"},
   {"id":"devin","provider":"devin","kind":"cli_seat","accounts":2,"eligibleAccounts":1,"status":"ok",
    "windowLabel":"month","observedRuns":2},
   {"id":"cursor-models","provider":"cursor","kind":"cli_seat_budget","accounts":2,"eligibleAccounts":2,"status":"ok",
    "windowLabel":"month","spentUsd":10.1,"budgetUsd":null,"monthlyRemainingPercent":null,"unbudgetedAccounts":2},
   {"id":"cursor-other","provider":"cursor","kind":"cli_seat_budget","accounts":2,"eligibleAccounts":2,"status":"ok",
    "windowLabel":"month","spentUsd":6.0,"budgetUsd":70,"monthlyRemainingPercent":91.4,"unbudgetedAccounts":1}]}
  """

  func testAllCountsEveryAccountAndMakerScopesShowTheirPoolsAndAccounts() throws {
    let decoder = APIClient.makeDecoder()
    let seats = try decoder.decode(SeatAccountsResponse.self, from: Data(seatsJSON.utf8)).accounts
    let pools = try decoder.decode(PoolsDocument.self, from: Data(poolsJSON.utf8)).pools
    var accounts = try loadAccountsFixture()  // 3 Claude, 5 Codex
    accounts.append(makeTestAccount(id: "kimi-1", provider: "kimi"))
    accounts.append(makeTestAccount(id: "glm-1", provider: "glm"))

    let counts = ProviderScope.counts(in: accounts, seats: seats)
    XCTAssertEqual(counts[.all], 14)
    XCTAssertEqual(counts[.anthropic], 3)
    XCTAssertEqual(counts[.openai], 5)
    XCTAssertEqual(counts[.cursor], 2)
    XCTAssertEqual(counts[.devin], 2)
    XCTAssertEqual(counts[.other], 2)
    XCTAssertEqual(ProviderScope.allCases.map(\.label), ["All", "Codex", "Claude", "Cursor", "Devin", "Other"])
    XCTAssertTrue(ProviderScope.other.includes(accounts.first { $0.provider == "kimi" }!))
    XCTAssertEqual(ProviderScope.cursor.filter(accounts).count, 0)

    XCTAssertEqual(MakerRows.lines(for: .cursor, seats: seats, pools: pools), [
      "Cursor models: $10.10 spent, no published size",
      "Other models: $6.00 of $70, 91% left",
      "cursor-gmail: tier unknown, ready",
      "cursor-main: Pro+, ready",
    ])
    XCTAssertEqual(MakerRows.lines(for: .devin, seats: seats, pools: pools), [
      "Devin: 1 of 2 ready, 2 runs in 24 h",
      "devin-kinetic: disabled",
      "devin-main: ready",
    ])
  }
}
