import XCTest
import Darwin
@testable import AgentLB

final class ServiceControllerTests: XCTestCase {

  // MARK: - Command builders

  func testStatusCommand() {
    XCTAssertEqual(
      ServiceController.statusCommand(),
      ["launchctl", "list", "com.aneyman.agent-lb"]
    )
  }

  func testStartCommandLoaded() {
    let cmd = ServiceController.startCommand(loaded: true)
    XCTAssertEqual(cmd[0], "launchctl")
    XCTAssertEqual(cmd[1], "kickstart")
    // domainTarget uses runtime uid — assert prefix and suffix, not literal 501
    let target = try? XCTUnwrap(cmd.last)
    XCTAssertTrue(target?.hasPrefix("gui/") ?? false,
                  "Expected domainTarget to start with 'gui/'")
    XCTAssertTrue(target?.hasSuffix("com.aneyman.agent-lb") ?? false,
                  "Expected domainTarget to end with the label")
    // Must NOT be "load" (that's for unloaded path)
    XCTAssertFalse(cmd.contains("load"))
  }

  func testStartCommandNotLoaded() {
    let cmd = ServiceController.startCommand(loaded: false)
    XCTAssertEqual(cmd[0], "launchctl")
    XCTAssertEqual(cmd[1], "load")
    let path = try? XCTUnwrap(cmd.last)
    XCTAssertTrue(path?.hasSuffix("com.aneyman.agent-lb.plist") ?? false,
                  "Expected plist path to end with 'com.aneyman.agent-lb.plist'")
    XCTAssertFalse(path?.hasPrefix("~") ?? true,
                   "Tilde should be expanded in plistPath")
  }

  func testRestartCommand() {
    let cmd = ServiceController.restartCommand()
    XCTAssertEqual(cmd[0], "launchctl")
    XCTAssertEqual(cmd[1], "kickstart")
    XCTAssertTrue(cmd.contains("-k"), "restartCommand must include -k flag")
  }

  func testStopCommand() {
    let cmd = ServiceController.stopCommand()
    XCTAssertEqual(cmd[0], "launchctl")
    XCTAssertEqual(cmd[1], "unload")
    let path = try? XCTUnwrap(cmd.last)
    XCTAssertTrue(path?.hasSuffix("com.aneyman.agent-lb.plist") ?? false)
  }

  func testDomainTargetUsesRuntimeUID() {
    let target = ServiceController.domainTarget
    let uid = getuid()
    XCTAssertTrue(target.contains("gui/\(uid)/"),
                  "domainTarget must embed the runtime uid, not a hardcoded value")
  }

  // MARK: - PID-column parser

  func testPIDParserLoaded() {
    XCTAssertTrue(ServiceController.parsePIDColumn("123\t0\tcom.aneyman.agent-lb"))
  }

  func testPIDParserStopped() {
    XCTAssertFalse(ServiceController.parsePIDColumn("-\t0\tcom.aneyman.agent-lb"))
  }

  func testPIDParserEmpty() {
    XCTAssertFalse(ServiceController.parsePIDColumn(""))
  }

  func testPIDParserGarbage() {
    XCTAssertFalse(ServiceController.parsePIDColumn("garbage"))
    XCTAssertFalse(ServiceController.parsePIDColumn("PID\tStatus\tLabel"))
  }
}
