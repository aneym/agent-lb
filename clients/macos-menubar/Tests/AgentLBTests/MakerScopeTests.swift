import XCTest
@testable import AgentLB



final class MakerScopeTests: XCTestCase {
  private let seatsJSON = """
  {"stateUpdatedAt":"2026-09-30T02:40:00Z","source":"seat_state","accounts":[
   {"id":"cursor-main","vendor":"cursor","enabled":true,"authOk":true,"tier":"Ultra","ready":true,
    "cooldowns":{"cursor-other":"2099-10-30T04:00:00Z"},
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
   {"id":"cursor","provider":"cursor","kind":"cli_seat","accounts":2,"eligibleAccounts":2,"status":"ok"},
   {"id":"cursor-models","provider":"cursor","kind":"cli_seat_budget","accounts":2,"eligibleAccounts":2,"status":"ok",
    "windowLabel":"month","percentUsed":2,"percentSource":"estimate","cycleResetAt":"2099-10-30T04:00:00Z"},
   {"id":"cursor-other","provider":"cursor","kind":"cli_seat_budget","accounts":2,"eligibleAccounts":2,"status":"ok",
    "windowLabel":"month","percentUsed":100,"percentSource":"vendor","cycleResetAt":"2099-10-30T04:00:00Z"}]}
  """

  func testLegacySeatPayloadCountsSeatsAndRendersRowsAndCards() throws {
    let decoder = APIClient.makeDecoder()
    let seats = try decoder.decode(SeatAccountsResponse.self, from: Data(seatsJSON.utf8)).accounts
    let pools = try decoder.decode(PoolsDocument.self, from: Data(poolsJSON.utf8)).pools
    var accounts = try loadAccountsFixture()
    accounts.append(makeTestAccount(id: "kimi-1", provider: "kimi"))
    accounts.append(makeTestAccount(id: "glm-1", provider: "glm"))

    let counts = ProviderScope.counts(in: accounts, seats: seats)
    XCTAssertEqual(counts[.all], 14)
    XCTAssertEqual(counts[.anthropic], 3)
    XCTAssertEqual(counts[.openai], 5)
    XCTAssertEqual(counts[.cursor], 2)
    XCTAssertEqual(counts[.devin], 2)
    XCTAssertEqual(counts[.other], 2)
    XCTAssertEqual(counts[.muse], 0)
    XCTAssertEqual(ProviderScope.allCases.map(\.label), ["All", "Codex", "Claude", "Cursor", "Devin", "Muse", "Other"])
    XCTAssertTrue(ProviderScope.other.includes(accounts.first { $0.provider == "kimi" }!))
    XCTAssertEqual(ProviderScope.cursor.filter(accounts).count, 0)

    let now = Format.iso8601.date(from: "2026-09-30T03:00:00Z")!
    let cursorRows = AccountFilter(sort: .nameAsc).apply(to: [], seats: ProviderScope.cursor.filter(seats), now: now)
    XCTAssertEqual(cursorRows.map(\.id), ["seat:cursor-gmail", "seat:cursor-main"])
    let devinRows = AccountFilter(sort: .nameAsc).apply(to: [], seats: ProviderScope.devin.filter(seats), now: now)
    XCTAssertEqual(devinRows.map(\.id), ["seat:devin-kinetic", "seat:devin-main"])
    XCTAssertEqual(AccountFilter(status: .paused).apply(to: seats, now: now).map(\.id), ["devin-kinetic"])
    XCTAssertEqual(AccountFilter(status: .rateLimited).apply(to: seats, now: now).map(\.id), ["cursor-main"])
    XCTAssertEqual(AccountFilter(query: "gmail").apply(to: seats, now: now).map(\.id), ["cursor-gmail"])

    let main = try XCTUnwrap(seats.first { $0.id == "cursor-main" })
    XCTAssertEqual(RowPresentation.forSeat(main), .rateLimited(Format.iso8601.date(from: "2099-10-30T04:00:00Z")))
    XCTAssertEqual(SeatAccountRow(seat: main, now: now).trailingText, "limited · Oct 30, 12:00 AM")

    let cursor = SeatVendorSummary.build(scope: .cursor, pools: pools, seats: seats, summary: nil, now: now)
    XCTAssertEqual(cursor.cards.map(\.id), ["cursor-models", "cursor-other"])
    XCTAssertEqual(cursor.cards.map(\.title), ["MONTHLY LIMIT", "MONTHLY LIMIT"])
    XCTAssertEqual(cursor.cards.map(\.detail), ["Cursor models · Grok, Composer", "Other models"])
    XCTAssertEqual(cursor.cards.map(\.sourceText), ["est", "as of —"])
    XCTAssertEqual(cursor.cards.map(\.remainingPercent), [98, 0])

    let devin = SeatVendorSummary.build(scope: .devin, pools: pools, seats: seats, summary: nil, now: now)
    XCTAssertTrue(devin.cards.isEmpty)
    XCTAssertEqual(devin.quietLine, "No usage reading from Devin · 2 runs in 24 h")
    XCTAssertEqual(devin.seatsText, "1/2 seats")
  }
}
