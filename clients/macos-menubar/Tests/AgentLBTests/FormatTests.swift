import XCTest
@testable import AgentLB

final class FormatTests: XCTestCase {

  // Fixed reference point: avoids time-dependent flakiness
  private let now = Date(timeIntervalSince1970: 1_000_000)

  // MARK: - countdown

  func testCountdownUnderHour() {
    // +15 min → "0:15" (0 hours, 15 minutes, h:mm format)
    let target = now.addingTimeInterval(15 * 60)
    XCTAssertEqual(Format.countdown(to: target, relativeTo: now), "0:15")
  }

  func testCountdownOverHour() {
    // +205 min = 3h 25m
    let target = now.addingTimeInterval(205 * 60)
    XCTAssertEqual(Format.countdown(to: target, relativeTo: now), "3h 25m")
  }

  func testCountdownExactlyOneHour() {
    let target = now.addingTimeInterval(60 * 60)
    XCTAssertEqual(Format.countdown(to: target, relativeTo: now), "1h 0m")
  }

  func testCountdownPastDateClampsToZero() {
    let past = now.addingTimeInterval(-60)
    XCTAssertEqual(Format.countdown(to: past, relativeTo: now), "0:00")
  }

  // MARK: - countdownCompact (§9.3 account-row window grid)

  func testCountdownCompactUnderHour() {
    let target = now.addingTimeInterval(15 * 60)
    XCTAssertEqual(Format.countdownCompact(to: target, relativeTo: now), "0:15")
  }

  func testCountdownCompactHours() {
    // 23h 30m → "23h" (whole hours only)
    let target = now.addingTimeInterval(23.5 * 3600)
    XCTAssertEqual(Format.countdownCompact(to: target, relativeTo: now), "23h")
  }

  func testCountdownCompactDays() {
    // 60h → "2d" (≥ 48h switches to days)
    let target = now.addingTimeInterval(60 * 3600)
    XCTAssertEqual(Format.countdownCompact(to: target, relativeTo: now), "2d")
  }

  func testCountdownCompactPastDateClampsToZero() {
    let past = now.addingTimeInterval(-60)
    XCTAssertEqual(Format.countdownCompact(to: past, relativeTo: now), "0:00")
  }

  // MARK: - relativeAge

  func testRelativeAgeSeconds() {
    let past = now.addingTimeInterval(-12)
    XCTAssertEqual(Format.relativeAge(past, relativeTo: now), "12s ago")
  }

  func testRelativeAgeMinutes() {
    let past = now.addingTimeInterval(-120)
    XCTAssertEqual(Format.relativeAge(past, relativeTo: now), "2m ago")
  }

  func testRelativeAgeHours() {
    let past = now.addingTimeInterval(-7200)
    XCTAssertEqual(Format.relativeAge(past, relativeTo: now), "2h ago")
  }

  // MARK: - usd

  func testUsdLargeValue() {
    XCTAssertEqual(Format.usd(6159.252347), "$6,159.25")
  }

  func testUsdSmallValue() {
    // Values < 0.01 use 4 decimal places: 0.009603 → $0.0096
    XCTAssertEqual(Format.usd(0.009603), "$0.0096")
  }

  func testUsdNormalValue() {
    XCTAssertEqual(Format.usd(0.0210), "$0.02")
  }

  // MARK: - compact

  func testCompactBelowThreshold() {
    XCTAssertEqual(Format.compact(999), "999")
  }

  func testCompactExactThousand() {
    XCTAssertEqual(Format.compact(1000), "1.0k")
  }

  func testCompactDesignExample() {
    // Design doc: compact(60805) == "60.8k" (floor, not round)
    XCTAssertEqual(Format.compact(60805), "60.8k")
  }

  // MARK: - percent

  func testPercentRendersInteger() {
    // Design §1.1 shows integer percents ("95%", "62%") — never one decimal
    XCTAssertEqual(Format.percent(95.73), "96%")
    XCTAssertEqual(Format.percent(62.0), "62%")
    XCTAssertEqual(Format.percent(0), "0%")
  }

  // MARK: - latency

  func testLatencySeconds() {
    XCTAssertEqual(Format.latency(12200), "12.2s")
  }

  func testLatencyMilliseconds() {
    XCTAssertEqual(Format.latency(840), "840ms")
  }

  func testLatencyAtBoundary() {
    XCTAssertEqual(Format.latency(1000), "1.0s")
  }

  // MARK: - tokens

  func testTokensSmall() {
    XCTAssertEqual(Format.tokens(713), "713 tok")
  }

  func testTokensLarge() {
    XCTAssertEqual(Format.tokens(1200), "1.2k tok")
  }

  // MARK: - shortAge (§8.2 header chip + Recent trailing age)

  func testShortAgeSeconds() {
    XCTAssertEqual(Format.shortAge(now.addingTimeInterval(-1), relativeTo: now), "1s")
  }

  func testShortAgeMinutes() {
    XCTAssertEqual(Format.shortAge(now.addingTimeInterval(-120), relativeTo: now), "2m")
  }

  func testShortAgeHours() {
    XCTAssertEqual(Format.shortAge(now.addingTimeInterval(-7200), relativeTo: now), "2h")
  }

  // MARK: - compactLarge (§8.2 token counts)

  func testCompactLargeBillions() {
    // summary.json fixture value: tokensSecondaryWindow
    XCTAssertEqual(Format.compactLarge(3_834_168_753), "3.8B")
  }

  func testCompactLargeMillions() {
    XCTAssertEqual(Format.compactLarge(12_460_000), "12.4M")
  }

  func testCompactLargeThousands() {
    XCTAssertEqual(Format.compactLarge(60805), "60.8k")
  }

  func testCompactLargeBelowThousand() {
    XCTAssertEqual(Format.compactLarge(999), "999")
  }

  // MARK: - credits (§8.2 pool cards)

  func testCompactCreditsRoundsToOneDecimal() {
    // 7467 → "7.5k" per the §8.2 example (rounded, unlike compact's floor)
    XCTAssertEqual(Format.compactCredits(7467), "7.5k")
    XCTAssertEqual(Format.compactCredits(7800), "7.8k")
  }

  func testCompactCreditsBelowThousand() {
    XCTAssertEqual(Format.compactCredits(100), "100")
  }

  func testCreditsLine() {
    XCTAssertEqual(Format.credits(remaining: 7467, capacity: 7800), "7.5k / 7.8k cr")
  }

  // MARK: - cachedPercent (§8.2 metrics strip)

  func testCachedPercentFromFixtureValues() {
    XCTAssertEqual(
      Format.cachedPercent(cached: 3_050_279_380, total: 3_834_168_753),
      "80% cached"
    )
  }

  func testCachedPercentZeroTotalIsNil() {
    XCTAssertNil(Format.cachedPercent(cached: 100, total: 0))
  }
}
