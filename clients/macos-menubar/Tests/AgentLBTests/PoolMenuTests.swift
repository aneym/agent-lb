import AppKit
import SwiftUI
import XCTest

@testable import AgentLB

final class PoolMenuTests: XCTestCase {
  private let zone = TimeZone(identifier: "America/New_York")!

  private var now: Date {
    date("2026-09-30T14:31:00Z")
  }

  func testRowsMatchTodaysPools() {
    let rows = PoolMenu.rows(pools: fixturePools(), now: now, timeZone: zone)
    XCTAssertEqual(rows.map(\.name), [
      "Claude", "OpenAI", "Cursor models", "Cursor other", "Devin",
    ])
    XCTAssertEqual(rows.map(\.value), [
      "28% · 3/8", "17% · 4/5", "98% · 1/1", "out", "50% · 1/2",
    ])
    XCTAssertEqual(rows.map(\.tone), [.warning, .warning, .success, .danger, .success])
    XCTAssertEqual(rows[0].subline, "+1 at 12:59 PM")
    XCTAssertEqual(rows[1].subline, "+3 Oct 3, 12:58 PM")
    XCTAssertEqual(rows[2].subline, "estimate · resets Oct 30")
    XCTAssertEqual(rows[3].subline, "back Oct 30")
    XCTAssertNil(rows[4].subline)
    XCTAssertFalse(rows[0].ticks.isEmpty)
    XCTAssertEqual(
      PoolMenu.footer(plan: fixturePlan()),
      "Code → Grok 4.7 low · Edits → Composer 2.5"
    )
    XCTAssertNil(PoolMenu.footer(plan: nil))
  }

  func testFableLosesToGeneralInEitherOrder() {
    let pools = fixturePools()
    let general = pools[0]
    let fable = pools[1]
    XCTAssertEqual(general.id, "anthropic-general")
    XCTAssertEqual(fable.id, "anthropic-fable")
    let forward = PoolMenu.rows(pools: pools, now: now, timeZone: zone)
    let reversed = PoolMenu.rows(
      pools: [fable, general] + pools.dropFirst(2),
      now: now,
      timeZone: zone
    )
    XCTAssertEqual(forward[0].value, "28% · 3/8")
    XCTAssertEqual(reversed[0].value, "28% · 3/8")
    XCTAssertEqual(forward[0].name, "Claude")
  }

  @MainActor
  func testRendersPoolSectionSnapshot() throws {
    let view = PoolSection(
      summary: nil,
      projections: nil,
      scope: .all,
      scopedAccounts: [],
      arbitrage: nil,
      hasError: false,
      retry: {},
      menuPools: fixturePools(),
      plan: fixturePlan(),
      menuNow: now,
      menuTimeZone: zone
    )
    .frame(width: 320)
    .padding(12)
    .background(Color.white)

    let renderer = ImageRenderer(content: view)
    renderer.scale = 2
    renderer.proposedSize = ProposedViewSize(width: 320, height: nil)
    guard let image = renderer.nsImage else {
      XCTFail("ImageRenderer produced no image")
      return
    }
    guard let tiff = image.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let png = rep.representation(using: .png, properties: [:]) else {
      XCTFail("Could not encode snapshot PNG")
      return
    }
    let directory = URL(fileURLWithPath: #filePath)
      .deletingLastPathComponent()
      .deletingLastPathComponent()
      .appendingPathComponent("Snapshots", isDirectory: true)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let url = directory.appendingPathComponent("pool-section.png")
    try png.write(to: url)
    XCTAssertGreaterThan(png.count, 1_000)
  }

  private func fixturePools() -> [PoolEntry] {
    let json = """
    {"pools":[
      {"id":"anthropic-general","provider":"anthropic","kind":"subscription","accounts":8,"eligibleAccounts":3,
       "status":"ok","weeklyRemainingPercent":28.4,"usableAccounts":3,"totalAccounts":8,
       "weeklyResetAt":"2026-10-07T12:00:00Z",
       "refills":[{"at":"2026-09-30T16:59:00Z","accounts":1,"remainingPercent":40}]},
      {"id":"anthropic-fable","provider":"anthropic","kind":"subscription","accounts":1,"eligibleAccounts":1,
       "status":"ok","weeklyRemainingPercent":4,"usableAccounts":1,"totalAccounts":1,
       "weeklyResetAt":"2026-10-07T12:00:00Z"},
      {"id":"openai-codex","provider":"openai","kind":"subscription","accounts":5,"eligibleAccounts":4,
       "status":"ok","aggregateRemainingPercent":16.6,"usableAccounts":4,"totalAccounts":5,
       "resetAt":"2026-10-06T22:05:00Z",
       "refills":[{"at":"2026-10-03T16:58:00Z","accounts":3,"remainingPercent":50}]},
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

  private func fixturePlan() -> PoolPlan {
    let json = """
    {"generatedAt":"2026-09-30T14:31:00Z","recommended":"balanced","levels":{
      "balanced":{
        "title":"Balanced","why":"Same accounts as today.","accounts":[],
        "ladders":{
          "implement":[{"id":"grok-low","model":"Grok 4.7 low","harness":"Cursor CLI"}],
          "mechanical":[{"id":"composer","model":"Composer 2.5","harness":"Cursor CLI"}]
        },
        "risk":"Watch."
      }
    }}
    """
    return try! APIClient.makeDecoder().decode(PoolPlan.self, from: Data(json.utf8))
  }

  private func date(_ iso: String) -> Date {
    Format.iso8601.date(from: iso)!
  }
}
