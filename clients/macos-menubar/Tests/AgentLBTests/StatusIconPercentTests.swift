import XCTest
@testable import AgentLB

final class StatusIconPercentTests: XCTestCase {
  func testStatusIconUsesWeeklyWindowBeforePrimaryWindow() {
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

    XCTAssertEqual(AppState.statusIconPercent(from: summary), 47)
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

    XCTAssertEqual(AppState.statusIconPercent(from: summary), 63)
  }

  func testStatusIconFallsBackToPrimaryWhenLongWindowIsMissing() {
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

    XCTAssertEqual(AppState.statusIconPercent(from: summary), 91)
  }
}
