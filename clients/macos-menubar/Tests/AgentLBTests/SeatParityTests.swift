import AppKit
import SwiftUI
import XCTest

@testable import AgentLB

struct SeatUsageExample: Decodable {
  let seatAccounts: SeatAccountsResponse
  let poolsExcerpt: [PoolEntry]
  let usageSummaryCursor: UsageSummary
}

func loadSeatUsageExample() throws -> SeatUsageExample {
  let url = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent()
    .deletingLastPathComponent()
    .deletingLastPathComponent()
    .appendingPathComponent("Sources/AgentLB/Fixtures/seat-usage.example.json")
  let data = try Data(contentsOf: url)
  return try APIClient.makeDecoder().decode(SeatUsageExample.self, from: data)
}

final class SeatParityTests: XCTestCase {
  private let now = Format.iso8601.date(from: "2026-10-16T14:05:00Z")!

  private func pools(_ example: SeatUsageExample) throws -> [PoolEntry] {
    let extra = """
    {"pools":[
      {"id":"cursor-other","provider":"cursor","kind":"cli_seat_budget","accounts":1,"eligibleAccounts":0,
       "totalAccounts":1,"usableAccounts":0,"status":"exhausted","windowLabel":"month","percentUsed":100,
       "percentSource":"vendor","cycleResetAt":"2026-10-30T00:00:00Z","quotaSource":"vendor",
       "windows":[{"id":"cursor-other","kind":"monthly","label":"Other models","remainingPercent":0,
         "usedPercent":100,"resetsAt":"2026-10-30T00:00:00Z","measuredAccounts":1,"source":"vendor"}]},
      {"id":"devin","provider":"devin","kind":"cli_seat","accounts":1,"eligibleAccounts":1,"totalAccounts":1,
       "usableAccounts":1,"status":"ok","observedRuns":2,"quotaSource":"vendor",
       "windows":[
         {"id":"daily","kind":"daily","label":"Daily","remainingPercent":80,"usedPercent":20,
          "resetsAt":"2026-10-17T04:00:00Z","measuredAccounts":1,"source":"vendor"},
         {"id":"weekly","kind":"weekly","label":"Weekly","remainingPercent":45,"usedPercent":55,
          "resetsAt":"2026-10-20T04:00:00Z","measuredAccounts":1,"source":"vendor"}]}
    ]}
    """
    let added = try APIClient.makeDecoder().decode(PoolsDocument.self, from: Data(extra.utf8)).pools
    return example.poolsExcerpt + added
  }

  private func summary(for scope: ProviderScope, example: SeatUsageExample) throws -> UsageSummary {
    if scope == .cursor { return example.usageSummaryCursor }
    let json = scope == .devin
      ? """
        {"provider":"devin","cost":{"currency":"USD","totalUsd7d":0.0},"metrics":{"requests7d":0},
         "seatActivity":{"periodHours":168,"runs":9,"ok":9,"tokensIn":null,"tokensOut":null,"tokensKnown":false,
          "valueUsd":null,"valueSource":null,"models":[{"model":"swe-1.5","runs":9}]}}
        """
      : """
        {"provider":"muse","cost":{"currency":"USD","totalUsd7d":0.0},"metrics":{"requests7d":0},
         "seatActivity":{"periodHours":168,"runs":3,"ok":3,"tokensIn":null,"tokensOut":null,"tokensKnown":false,
          "valueUsd":null,"valueSource":null,"models":[{"model":"muse-wonder-1.0","runs":3}]}}
        """
    return try APIClient.makeDecoder().decode(UsageSummary.self, from: Data(json.utf8))
  }

  func testContractFixtureDecodesEveryField() throws {
    let example = try loadSeatUsageExample()
    XCTAssertEqual(example.seatAccounts.quotaRefreshedAt, Format.iso8601.date(from: "2026-10-16T14:00:05Z"))
    let gmail = try XCTUnwrap(example.seatAccounts.accounts.first { $0.id == "cursor-gmail" })
    XCTAssertEqual(gmail.status, "rate_limited")
    XCTAssertEqual(gmail.planLabel, "Ultra")
    XCTAssertEqual(gmail.email, "seat-owner@example.invalid")
    XCTAssertEqual(gmail.primaryQuotaWindow?.id, "cursor-other")
    XCTAssertEqual(gmail.quotaWindows.map(\.label), ["Cursor models · Grok, Composer", "Other models"])
    XCTAssertEqual(gmail.today?.valueUsd, 3.41)
    XCTAssertEqual(gmail.recentRuns?.first?.ok, true)
    XCTAssertEqual(gmail.recentRuns?.first?.wallS, 140.2)
    let muse = try XCTUnwrap(example.seatAccounts.accounts.first { $0.id == "muse-main" })
    XCTAssertEqual(muse.quota?.source, "unavailable")
    XCTAssertEqual(muse.quota?.unavailableReason, "not_reported")
    XCTAssertNil(muse.today?.tokensIn)
    XCTAssertEqual(example.poolsExcerpt.first?.windows?.first?.measuredAccounts, 1)
    XCTAssertEqual(example.poolsExcerpt.last?.quotaSource, "unavailable")
    XCTAssertEqual(example.usageSummaryCursor.provider, "cursor")
    XCTAssertEqual(example.usageSummaryCursor.seatActivity?.runs, 31)
  }

  func testCursorTabShowsMonthlyWindowsAndNoFiveHourCard() throws {
    let example = try loadSeatUsageExample()
    let vendor = SeatVendorSummary.build(
      scope: .cursor, pools: try pools(example), seats: example.seatAccounts.accounts,
      summary: example.usageSummaryCursor, now: now
    )
    XCTAssertEqual(vendor.cards.map(\.title), ["MONTHLY LIMIT", "MONTHLY LIMIT"])
    XCTAssertEqual(vendor.cards.map(\.detail), ["Cursor models · Grok, Composer", "Other models"])
    XCTAssertEqual(vendor.cards.map(\.remainingPercent), [38, 0])
    XCTAssertEqual(vendor.cards.map(\.resetText), ["resets Oct 29, 8:00 PM", "resets Oct 29, 8:00 PM"])
    XCTAssertEqual(vendor.cards.map(\.sourceText), ["as of 10:00 AM", "as of 10:00 AM"])
    XCTAssertEqual(vendor.cards.map(\.seatsText), ["2/2 seats", "0/1 seat"])
    XCTAssertFalse(vendor.texts.contains { $0.contains("5-HOUR") })
    XCTAssertEqual(vendor.stripLines, ["7d · 31 runs · 2.1M in / 96.0k out · $17.85 est"])
  }

  func testDevinTabShowsDailyAndWeekly() throws {
    let example = try loadSeatUsageExample()
    let vendor = SeatVendorSummary.build(
      scope: .devin, pools: try pools(example), seats: example.seatAccounts.accounts,
      summary: try summary(for: .devin, example: example), now: now
    )
    XCTAssertEqual(vendor.cards.map(\.title), ["DAILY LIMIT", "WEEKLY LIMIT"])
    XCTAssertEqual(vendor.cards.map(\.detail), [nil, nil])
    XCTAssertEqual(vendor.cards.map(\.resetText), ["resets Oct 17, 12:00 AM", "resets Oct 20, 12:00 AM"])
    XCTAssertEqual(vendor.stripLines, ["7d · 9 runs · tokens not reported · value unknown"])
  }

  func testMuseTabIsQuietWithoutAReading() throws {
    let example = try loadSeatUsageExample()
    let vendor = SeatVendorSummary.build(
      scope: .muse, pools: try pools(example), seats: example.seatAccounts.accounts,
      summary: try summary(for: .muse, example: example), now: now
    )
    XCTAssertTrue(vendor.cards.isEmpty)
    XCTAssertEqual(vendor.quietLine, "Not reported by Muse · 0 runs in 24 h")
    XCTAssertEqual(vendor.stripLines, ["7d · 3 runs · tokens not reported · value unknown"])
    XCTAssertFalse(vendor.texts.contains { $0.contains("$0.0000") || $0.contains("$0.00") })
  }

  func testDevinCredentialUnavailableNamesTheReason() throws {
    let example = try loadSeatUsageExample()
    let json = """
    {"id":"devin","provider":"devin","kind":"cli_seat","accounts":1,"eligibleAccounts":1,"status":"ok",
     "quotaSource":"unavailable","windows":[]}
    """
    let pool = try APIClient.makeDecoder().decode(PoolEntry.self, from: Data(json.utf8))
    let devinJSON = """
    {"id":"devin-main","vendor":"devin","enabled":true,"authOk":true,"tier":"Devin Pro","ready":true,
     "lastDay":{"runs":4,"ok":4,"wallS":10,"tokensIn":0,"tokensOut":0},"status":"active",
     "quota":{"source":"unavailable","stale":false,"unavailableReason":"credential_unavailable","windows":[]}}
    """
    let seat = try APIClient.makeDecoder().decode(SeatAccount.self, from: Data(devinJSON.utf8))
    let vendor = SeatVendorSummary.build(
      scope: .devin, pools: [pool], seats: [seat], summary: try summary(for: .devin, example: example), now: now
    )
    XCTAssertTrue(vendor.cards.isEmpty)
    XCTAssertEqual(vendor.quietLine, "Usage credential unavailable · 4 runs in 24 h")
  }

  func testSeatRowsMatchSeatCountsAndUseSharedPresentation() throws {
    let example = try loadSeatUsageExample()
    let seats = example.seatAccounts.accounts
    let counts = ProviderScope.counts(in: [], seats: seats)
    for scope in ProviderScope.seatVendors {
      let rows = AccountFilter().apply(to: [], seats: scope.filter(seats), now: now)
      XCTAssertEqual(rows.count, counts[scope], scope.rawValue)
    }
    XCTAssertEqual(AccountFilter().apply(to: [], seats: seats, now: now).count, counts[.all])

    let gmail = try XCTUnwrap(seats.first { $0.id == "cursor-gmail" })
    let reset = Format.iso8601.date(from: "2026-10-30T00:00:00Z")
    XCTAssertEqual(RowPresentation.forSeat(gmail), .rateLimited(reset))
    XCTAssertEqual(SeatAccountRow(seat: gmail, now: now).trailingText, "limited · Oct 29, 8:00 PM")
    XCTAssertEqual(SeatAccountRow.cells(for: gmail).map(\.label), ["CURSOR", "OTHER"])
    XCTAssertEqual(SeatAccountRow.cells(for: gmail).map(\.percent), [38, 0])

    let devin = try XCTUnwrap(seats.first { $0.id == "devin-main" })
    XCTAssertEqual(SeatAccountRow.cells(for: devin).map(\.label), ["DAY", "WK"])
    XCTAssertEqual(SeatAccountRow.todayText(devin), "2 runs")
    XCTAssertEqual(SeatAccountRow.todayText(gmail), "6 runs · ~$3.41")

    let mask = PrivacyMask.build(enabled: true, accounts: [], seats: seats)
    XCTAssertEqual(mask.name(for: gmail, real: gmail.id), "Cursor 1")
    XCTAssertNil(mask.email(for: gmail))
    XCTAssertEqual(PrivacyMask.disabled.name(for: gmail, real: gmail.id), "cursor-gmail")

    XCTAssertEqual(AccountFilter(status: .rateLimited).apply(to: seats, now: now).map(\.id), ["cursor-gmail"])
    XCTAssertEqual(AccountFilter(query: "ultra").apply(to: seats, now: now).map(\.id), ["cursor-gmail"])
    XCTAssertEqual(
      AccountFilter(sort: .remainingAsc).apply(to: seats, now: now).map(\.id),
      ["cursor-gmail", "devin-main", "muse-main"]
    )
  }

  func testScopedStripNeverSaysAllProviders() throws {
    let example = try loadSeatUsageExample()
    for scope in ProviderScope.seatVendors {
      let vendor = SeatVendorSummary.build(
        scope: scope, pools: try pools(example), seats: example.seatAccounts.accounts,
        summary: try summary(for: scope, example: example), now: now
      )
      XCTAssertFalse(vendor.texts.contains { $0.contains("all providers") }, scope.rawValue)
      XCTAssertFalse(vendor.texts.contains { $0.contains("accts") }, scope.rawValue)
      XCTAssertEqual(
        PoolSection.metricsLineCount(scope: scope, summary: try summary(for: scope, example: example), hasArbitrage: false),
        vendor.stripLines.count
      )
    }
  }

  @MainActor
  func testRendersSeatVendorTabsLightAndDark() throws {
    let example = try loadSeatUsageExample()
    let allPools = try pools(example)
    let seats = example.seatAccounts.accounts
    let counts = ProviderScope.counts(in: [], seats: seats)
    let appState = AppState()
    let directory = URL(fileURLWithPath: #filePath)
      .deletingLastPathComponent()
      .deletingLastPathComponent()
      .appendingPathComponent("Snapshots", isDirectory: true)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

    for scope in ProviderScope.seatVendors {
      let scopedSeats = scope.filter(seats)
      let summary = try summary(for: scope, example: example)
      for scheme in [ColorScheme.light, .dark] {
        let view = VStack(spacing: 0) {
          ProviderScopeBar(scopeRaw: .constant(scope.rawValue), counts: counts)
            .frame(height: PanelMetrics.scopeControl)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
          Divider().opacity(0.5)
          ScopeTabContent(
            scope: scope,
            summary: summary,
            projections: nil,
            accounts: [],
            seats: scopedSeats,
            pools: allPools,
            plan: nil,
            recent: [],
            arbitrage: nil,
            listHeight: Double(scopedSeats.count) * PanelMetrics.accountRow + PanelMetrics.listPadding,
            poolHasError: false,
            accountsHaveError: false,
            recentHasError: false,
            retry: {},
            accountStatus: .constant(.all),
            accountQuery: .constant(""),
            searchVisible: .constant(false),
            recentExpanded: .constant(false),
            fixedNow: now
          )
        }
        .frame(width: PanelMetrics.width)
        .background(scheme == .dark ? Color(white: 0.13) : Color(white: 0.96))
        .environment(appState)
        .environment(\.colorScheme, scheme)

        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        renderer.proposedSize = ProposedViewSize(width: PanelMetrics.width, height: nil)
        let image = try XCTUnwrap(renderer.nsImage, "\(scope.rawValue) \(scheme)")
        let tiff = try XCTUnwrap(image.tiffRepresentation)
        let rep = try XCTUnwrap(NSBitmapImageRep(data: tiff))
        let png = try XCTUnwrap(rep.representation(using: .png, properties: [:]))
        let name = "seat-\(scope.rawValue)-\(scheme == .dark ? "dark" : "light").png"
        try png.write(to: directory.appendingPathComponent(name))
        XCTAssertGreaterThan(png.count, 5_000, name)
      }
    }
  }
}
