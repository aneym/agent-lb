import XCTest

@testable import AgentLB

final class HealthGraceTests: XCTestCase {
  func testRecentSuccessKeepsRunningOrDegradedStatus() {
    for status: AppState.ServiceStatus in [.running, .degraded] {
      XCTAssertEqual(
        AppState.statusAfterHealthFailure(
          currentStatus: status, lastSuccessAge: .seconds(10), isRemote: false, isLoaded: true
        ),
        status
      )
    }
  }

  func testExpiredSuccessMakesLocalServiceUnreachable() {
    XCTAssertEqual(
      AppState.statusAfterHealthFailure(
        currentStatus: .running, lastSuccessAge: .seconds(50), isRemote: false, isLoaded: true
      ),
      .unreachable
    )
  }

  func testRestartTimeoutWithoutSuccessMakesRunningServiceUnreachable() {
    // A successful restart invalidates health from the old process before polling begins.
    XCTAssertEqual(
      AppState.statusAfterHealthFailure(
        currentStatus: .running, lastSuccessAge: nil, isRemote: false, isLoaded: true
      ),
      .unreachable
    )
  }

  func testStartingStatusCannotGainGrace() {
    XCTAssertEqual(
      AppState.statusAfterHealthFailure(
        currentStatus: .starting, lastSuccessAge: .seconds(1), isRemote: false, isLoaded: true
      ),
      .unreachable
    )
  }

  func testUnloadedLocalJobStopsImmediately() {
    XCTAssertEqual(
      AppState.statusAfterHealthFailure(
        currentStatus: .running, lastSuccessAge: .seconds(1), isRemote: false, isLoaded: false
      ),
      .stopped
    )
  }

  func testRemoteServiceUsesGraceWithoutLocalJob() {
    XCTAssertEqual(
      AppState.statusAfterHealthFailure(
        currentStatus: .running, lastSuccessAge: .seconds(10), isRemote: true, isLoaded: false
      ),
      .running
    )
    XCTAssertEqual(
      AppState.statusAfterHealthFailure(
        currentStatus: .running, lastSuccessAge: .seconds(50), isRemote: true, isLoaded: false
      ),
      .unreachable
    )
  }

  func testStoppedAndUnreachableDoNotGainGrace() {
    for status: AppState.ServiceStatus in [.stopped, .unreachable] {
      XCTAssertEqual(
        AppState.statusAfterHealthFailure(
          currentStatus: status, lastSuccessAge: .seconds(1), isRemote: false, isLoaded: true
        ),
        .unreachable
      )
      XCTAssertEqual(
        AppState.statusAfterHealthFailure(
          currentStatus: status, lastSuccessAge: .seconds(1), isRemote: false, isLoaded: false
        ),
        .stopped
      )
      XCTAssertEqual(
        AppState.statusAfterHealthFailure(
          currentStatus: status, lastSuccessAge: .seconds(1), isRemote: true, isLoaded: false
        ),
        .unreachable
      )
    }
  }
}
