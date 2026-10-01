import AppKit
import SwiftUI
import XCTest

@testable import AgentLB

// Scenario (Opus, 2026-10-01; the implementer does not edit this file).
// Alex, 07:47 ET: the all-model pool status shows on every tab and overlaps
// ACCOUNTS. Pools belong on the All tab only, in the compact card design, and
// must fit the height PanelLayout reserves for the pool section.
final class PoolTilesScenarioTests: XCTestCase {
  private let zone = TimeZone(identifier: "America/New_York")!

  @MainActor
  func testOnlyTheAllTabShowsPools() {
    for scope in ProviderScope.allCases {
      let section = poolSection(scope: scope)
      XCTAssertEqual(section.showsPoolTiles, scope == .all, "scope \(scope.rawValue)")
    }
  }

  @MainActor
  func testAllTabPoolsFitTheReservedHeight() throws {
    let view = poolSection(scope: .all).frame(width: 292)
    let renderer = ImageRenderer(content: view)
    renderer.scale = 1
    renderer.proposedSize = ProposedViewSize(width: 292, height: nil)
    let image = try XCTUnwrap(renderer.nsImage)
    // PanelLayout.poolHeight without the 12 pt section padding: label,
    // spacing, the fixed card, spacing, one metrics line (summary is nil).
    let reserved = PanelMetrics.poolLabel + PanelMetrics.poolSpacing
      + PanelMetrics.poolCard + PanelMetrics.poolSpacing + PanelMetrics.metricsLine
    XCTAssertLessThanOrEqual(image.size.height, reserved + 0.5)
  }

  @MainActor
  private func poolSection(scope: ProviderScope) -> PoolSection {
    PoolSection(
      summary: nil,
      projections: nil,
      scope: scope,
      scopedAccounts: [],
      arbitrage: nil,
      hasError: false,
      retry: {},
      menuPools: fixturePools(),
      plan: nil,
      menuNow: Format.iso8601.date(from: "2026-09-30T14:31:00Z")!,
      menuTimeZone: zone
    )
  }

  private func fixturePools() -> [PoolEntry] {
    let json = """
    {"pools":[
      {"id":"anthropic-general","provider":"anthropic","kind":"subscription","accounts":8,"eligibleAccounts":3,
       "status":"ok","weeklyRemainingPercent":28.4,"usableAccounts":3,"totalAccounts":8,
       "weeklyResetAt":"2026-10-07T12:00:00Z",
       "refills":[{"at":"2026-09-30T16:59:00Z","accounts":1,"remainingPercent":40}]},
      {"id":"openai-codex","provider":"openai","kind":"subscription","accounts":5,"eligibleAccounts":4,
       "status":"ok","aggregateRemainingPercent":16.6,"usableAccounts":4,"totalAccounts":5,
       "resetAt":"2026-10-06T22:05:00Z"},
      {"id":"cursor-models","provider":"cursor","kind":"cli_seat_budget","accounts":1,"eligibleAccounts":1,
       "status":"ok","windowLabel":"month","percentUsed":2,"percentSource":"estimate",
       "cycleResetAt":"2026-10-30T04:00:00Z"},
      {"id":"cursor-other","provider":"cursor","kind":"cli_seat_budget","accounts":1,"eligibleAccounts":0,
       "status":"exhausted","windowLabel":"month","percentUsed":100,"percentSource":"vendor",
       "cycleResetAt":"2026-10-30T04:00:00Z"},
      {"id":"devin","provider":"devin","kind":"cli_seat","accounts":2,"eligibleAccounts":1,
       "status":"ok","weeklyRemainingPercent":50,"usableAccounts":1,"totalAccounts":2}
    ]}
    """
    return try! APIClient.makeDecoder().decode(PoolsDocument.self, from: Data(json.utf8)).pools
  }
}
