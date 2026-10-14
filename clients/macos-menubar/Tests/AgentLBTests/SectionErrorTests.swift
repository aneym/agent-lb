import Foundation
import XCTest

@testable import AgentLB







final class SectionErrorTests: XCTestCase {


  func testDirectCancellationErrorIsCancellation() {
    XCTAssertTrue(AppState.isCancellation(CancellationError()))
  }

  func testCancelledURLErrorIsCancellation() {
    XCTAssertTrue(AppState.isCancellation(URLError(.cancelled)))
  }

  func testTransportWrappedCancelledURLErrorIsCancellation() {
    XCTAssertTrue(AppState.isCancellation(APIError.transport(URLError(.cancelled))))
  }



  func testTimeoutIsNotCancellation() {
    XCTAssertFalse(AppState.isCancellation(APIError.timeout))
  }

  func testTimedOutURLErrorIsNotCancellation() {
    XCTAssertFalse(AppState.isCancellation(URLError(.timedOut)))
  }

  func testHTTPStatusFailureIsNotCancellation() {
    XCTAssertFalse(AppState.isCancellation(APIError.httpStatus(500, body: nil)))
  }

  func testTransportWrappedNonCancelledURLErrorIsNotCancellation() {
    XCTAssertFalse(
      AppState.isCancellation(APIError.transport(URLError(.networkConnectionLost)))
    )
  }

  func testServiceDownIsNotCancellation() {
    XCTAssertFalse(AppState.isCancellation(APIError.serviceDown))
  }



  func testCancellationPreservesExistingSectionState() {
    var errors: Set<AppState.Section> = [.pool]
    AppState.updateSectionError(.accounts, error: CancellationError(), in: &errors)
    XCTAssertEqual(errors, [.pool])
  }

  func testSuccessfulFetchClearsStaleSectionError() {
    var errors: Set<AppState.Section> = [.pool, .accounts]
    AppState.updateSectionError(.pool, error: nil, in: &errors)
    XCTAssertEqual(errors, [.accounts])
  }

  func testCompletedFailureSetsSectionError() {
    var errors: Set<AppState.Section> = []
    AppState.updateSectionError(.recent, error: APIError.timeout, in: &errors)
    XCTAssertEqual(errors, [.recent])
  }
}
