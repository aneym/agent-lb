import XCTest
@testable import AgentLB

final class StatusIconPercentTests: XCTestCase {
  func testStatusIconPassesPrimaryThroughAndUsesWeeklyWindowForLongWindow() {
    let summary = UsageSummary(
      primaryWindow: UsageWindow(
        remainingPercent: 91,
        capacityCredits: nil,
        remainingCredits: nil,
        resetAt: nil,
        windowMinutes: 300
      ),
      secondaryWindow: UsageWindow(
        remainingPercent: 47,
        capacityCredits: nil,
        remainingCredits: nil,
        resetAt: nil,
        windowMinutes: 10_080
      ),
      monthlyWindow: nil,
      cost: nil,
      metrics: nil
    )

    let percents = AppState.statusIconPercents(from: summary)
    XCTAssertEqual(percents.primary, 91)
    XCTAssertEqual(percents.longWindow, 47)
  }

  func testStatusIconUsesMonthlyWindowWhenWeeklyWindowIsMissing() {
    let summary = UsageSummary(
      primaryWindow: UsageWindow(
        remainingPercent: 91,
        capacityCredits: nil,
        remainingCredits: nil,
        resetAt: nil,
        windowMinutes: 300
      ),
      secondaryWindow: nil,
      monthlyWindow: UsageWindow(
        remainingPercent: 63,
        capacityCredits: nil,
        remainingCredits: nil,
        resetAt: nil,
        windowMinutes: 43_200
      ),
      cost: nil,
      metrics: nil
    )

    let percents = AppState.statusIconPercents(from: summary)
    XCTAssertEqual(percents.primary, 91)
    XCTAssertEqual(percents.longWindow, 63)
  }

  func testStatusIconLongWindowNilWhenBothWeeklyAndMonthlyAreMissing() {
    let summary = UsageSummary(
      primaryWindow: UsageWindow(
        remainingPercent: 91,
        capacityCredits: nil,
        remainingCredits: nil,
        resetAt: nil,
        windowMinutes: 300
      ),
      secondaryWindow: nil,
      monthlyWindow: nil,
      cost: nil,
      metrics: nil
    )

    let percents = AppState.statusIconPercents(from: summary)
    XCTAssertEqual(percents.primary, 91)
    XCTAssertNil(percents.longWindow)
  }

  func testStatusIconPercentsNilWhenAllWindowsAreMissing() {
    let summary = UsageSummary(
      primaryWindow: nil,
      secondaryWindow: nil,
      monthlyWindow: nil,
      cost: nil,
      metrics: nil
    )

    let percents = AppState.statusIconPercents(from: summary)
    XCTAssertNil(percents.primary)
    XCTAssertNil(percents.longWindow)
  }

  func testStatusIconPercentsBothPresentOnPlainSummary() {
    let summary = UsageSummary(
      primaryWindow: UsageWindow(
        remainingPercent: 80,
        capacityCredits: nil,
        remainingCredits: nil,
        resetAt: nil,
        windowMinutes: 300
      ),
      secondaryWindow: UsageWindow(
        remainingPercent: 55,
        capacityCredits: nil,
        remainingCredits: nil,
        resetAt: nil,
        windowMinutes: 10_080
      ),
      monthlyWindow: nil,
      cost: nil,
      metrics: nil
    )

    let percents = AppState.statusIconPercents(from: summary)
    XCTAssertEqual(percents.primary, 80)
    XCTAssertEqual(percents.longWindow, 55)
  }
}
