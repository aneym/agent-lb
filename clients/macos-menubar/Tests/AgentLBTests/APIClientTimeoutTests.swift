import Foundation
import XCTest

@testable import AgentLB

final class APIClientTimeoutTests: XCTestCase {
  func testLoopbackHostsUseFastLocalReadEnvelope() throws {
    for raw in ["http://127.0.0.1:2455", "http://localhost:2455", "http://[::1]:2455"] {
      let url = try XCTUnwrap(URL(string: raw))
      XCTAssertEqual(APIReadTimeoutPolicy.forBaseURL(url), .local)
    }
  }

  func testTailnetHostUsesRemoteReadEnvelope() throws {
    let url = try XCTUnwrap(URL(string: "https://studio.tailf266ac.ts.net:2455"))
    XCTAssertEqual(APIReadTimeoutPolicy.forBaseURL(url), .remote)
  }

  func testRemoteEnvelopeExceedsLocalWithoutBecomingUnbounded() {
    XCTAssertGreaterThan(APIReadTimeoutPolicy.remote.request, APIReadTimeoutPolicy.local.request)
    XCTAssertGreaterThan(APIReadTimeoutPolicy.remote.resource, APIReadTimeoutPolicy.remote.request)
    XCTAssertLessThanOrEqual(APIReadTimeoutPolicy.remote.resource, 20)
  }
}
