import Foundation
import XCTest

@testable import AgentLB

final class APIClientTimeoutTests: XCTestCase {
  func testLoopbackHostsUseFastHealthEnvelopeAndDatabaseReadEnvelope() throws {
    for raw in ["http://127.0.0.1:2455", "http://localhost:2455", "http://[::1]:2455"] {
      let url = try XCTUnwrap(URL(string: raw))
      XCTAssertEqual(APIHealthTimeoutPolicy.forBaseURL(url), .local)
      XCTAssertEqual(APIReadTimeoutPolicy.forBaseURL(url), .local)
    }

    XCTAssertEqual(APIHealthTimeoutPolicy.local.request, 3)
    XCTAssertEqual(APIHealthTimeoutPolicy.local.resource, 5)
    XCTAssertEqual(APIReadTimeoutPolicy.local.request, 15)
    XCTAssertEqual(APIReadTimeoutPolicy.local.resource, 20)
  }

  func testTailnetHostUsesBoundedRemoteHealthAndReadEnvelopes() throws {
    let url = try XCTUnwrap(URL(string: "https://studio.tailf266ac.ts.net:2455"))
    XCTAssertEqual(APIHealthTimeoutPolicy.forBaseURL(url), .remote)
    XCTAssertEqual(APIReadTimeoutPolicy.forBaseURL(url), .remote)

    XCTAssertEqual(APIHealthTimeoutPolicy.remote.request, 15)
    XCTAssertEqual(APIHealthTimeoutPolicy.remote.resource, 20)
    XCTAssertEqual(APIReadTimeoutPolicy.remote.request, 15)
    XCTAssertEqual(APIReadTimeoutPolicy.remote.resource, 20)
  }

  func testRemoteEnvelopesRemainBounded() {
    XCTAssertGreaterThan(APIHealthTimeoutPolicy.remote.request, APIHealthTimeoutPolicy.local.request)
    XCTAssertGreaterThan(APIHealthTimeoutPolicy.remote.resource, APIHealthTimeoutPolicy.remote.request)
    XCTAssertLessThanOrEqual(APIHealthTimeoutPolicy.remote.resource, 20)
    XCTAssertLessThanOrEqual(APIReadTimeoutPolicy.remote.resource, 20)
  }
}
