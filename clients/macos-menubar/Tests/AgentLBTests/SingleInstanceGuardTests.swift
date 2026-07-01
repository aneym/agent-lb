import XCTest
import Darwin
@testable import AgentLB

final class SingleInstanceGuardTests: XCTestCase {

  private var tempDirectory: URL!

  override func setUp() {
    super.setUp()
    tempDirectory = FileManager.default.temporaryDirectory
      .appendingPathComponent("SingleInstanceGuardTests-\(UUID().uuidString)", isDirectory: true)
  }

  override func tearDown() {
    SingleInstanceGuard.releaseForTesting()
    try? FileManager.default.removeItem(at: tempDirectory)
    tempDirectory = nil
    super.tearDown()
  }

  func testAcquireSucceedsOnFreshLock() {
    let lockURL = tempDirectory.appendingPathComponent("menubar.lock")
    XCTAssertTrue(SingleInstanceGuard.acquire(lockURL: lockURL))
  }

  func testSecondAcquireFailsWhileHeld() {
    let lockURL = tempDirectory.appendingPathComponent("menubar.lock")
    XCTAssertTrue(SingleInstanceGuard.acquire(lockURL: lockURL))

    // flock is associated with the open file description, not the process,
    // so a second fd on the same file conflicts even from within this
    // process. attempts: 1 avoids paying the retry delay for an expected
    // failure.
    XCTAssertFalse(SingleInstanceGuard.acquire(lockURL: lockURL, attempts: 1))
  }

  func testReacquireSucceedsAfterRelease() {
    let lockURL = tempDirectory.appendingPathComponent("menubar.lock")
    XCTAssertTrue(SingleInstanceGuard.acquire(lockURL: lockURL))

    SingleInstanceGuard.releaseForTesting()

    XCTAssertTrue(SingleInstanceGuard.acquire(lockURL: lockURL))
  }

  func testAcquireCreatesMissingDirectory() {
    let nestedDirectory = tempDirectory.appendingPathComponent("not-yet-created", isDirectory: true)
    let lockURL = nestedDirectory.appendingPathComponent("menubar.lock")

    var isDirectory: ObjCBool = false
    XCTAssertFalse(FileManager.default.fileExists(atPath: nestedDirectory.path, isDirectory: &isDirectory))

    XCTAssertTrue(SingleInstanceGuard.acquire(lockURL: lockURL))
    XCTAssertTrue(FileManager.default.fileExists(atPath: nestedDirectory.path, isDirectory: &isDirectory))
    XCTAssertTrue(isDirectory.boolValue)
  }
}
