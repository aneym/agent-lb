import Foundation
import Darwin

// MARK: - Errors

enum ServiceError: Error, Sendable {
  case commandFailed(exit: Int32, stderr: String)
}

// MARK: - ServiceController

// NOTE: stop() unloads only the main plist.  The watchdog
// (com.aneyman.agent-lb-watchdog) monitors the service and may relaunch it
// automatically.  We deliberately never touch the watchdog plist; the Stop
// action presents a confirmation: "The watchdog may restart the service.
// Stop anyway?" before calling stop().
struct ServiceController: Sendable {

  static let label = "com.aneyman.agent-lb"

  static var plistPath: String {
    (NSString("~/Library/LaunchAgents/com.aneyman.agent-lb.plist")
      as NSString).expandingTildeInPath
  }

  static var domainTarget: String {
    "gui/\(getuid())/\(label)"
  }

  // MARK: - Pure command builders (unit-tested)

  static func statusCommand() -> [String] {
    ["launchctl", "list", label]
  }

  /// - Parameter loaded: true when the plist is already loaded into launchd
  ///   (use kickstart); false when it needs to be loaded first.
  static func startCommand(loaded: Bool) -> [String] {
    if loaded {
      return ["launchctl", "kickstart", domainTarget]
    } else {
      return ["launchctl", "load", plistPath]
    }
  }

  static func restartCommand() -> [String] {
    ["launchctl", "kickstart", "-k", domainTarget]
  }

  static func stopCommand() -> [String] {
    ["launchctl", "unload", plistPath]
  }

  // MARK: - PID-column parser (unit-tested)

  /// Parses one line of `launchctl list <label>` output.
  /// Format: `PID\tLastExitStatus\tLabel`
  /// Returns true when the first column is a numeric PID (not "-").
  static func parsePIDColumn(_ line: String) -> Bool {
    let parts = line.split(separator: "\t", omittingEmptySubsequences: false)
    guard let first = parts.first else { return false }
    let pid = String(first)
    return pid != "-" && Int(pid) != nil
  }

  // MARK: - Execution

  func isLoadedWithPID() async -> Bool {
    guard let output = try? await run(ServiceController.statusCommand()) else {
      return false
    }
    // launchctl list <label> returns a single line when found
    for line in output.split(separator: "\n", omittingEmptySubsequences: true) {
      if ServiceController.parsePIDColumn(String(line)) { return true }
    }
    return false
  }

  /// True when the job is loaded into launchd at all (PID numeric *or* "-"):
  /// `launchctl list <label>` exits 0 for any loaded job and non-zero only
  /// when the label is unknown. Loaded-but-not-running jobs must use
  /// kickstart — `launchctl load` fails ("already loaded") in that state.
  func isLoaded() async -> Bool {
    (try? await run(ServiceController.statusCommand())) != nil
  }

  func start() async throws {
    let loaded = await isLoaded()
    try await run(ServiceController.startCommand(loaded: loaded))
  }

  func restart() async throws {
    try await run(ServiceController.restartCommand())
  }

  func stop() async throws {
    try await run(ServiceController.stopCommand())
  }

  // MARK: - Private

  @discardableResult
  private func run(_ args: [String]) async throws -> String {
    guard let first = args.first else { return "" }
    return try await withCheckedThrowingContinuation { continuation in
      let process = Process()
      process.executableURL = URL(filePath: "/bin/\(first)")
      process.arguments = Array(args.dropFirst())

      let stdoutPipe = Pipe()
      let stderrPipe = Pipe()
      process.standardOutput = stdoutPipe
      process.standardError = stderrPipe

      process.terminationHandler = { p in
        let out = String(data: stdoutPipe.fileHandleForReading.readDataToEndOfFile(),
                         encoding: .utf8) ?? ""
        let err = String(data: stderrPipe.fileHandleForReading.readDataToEndOfFile(),
                         encoding: .utf8) ?? ""
        if p.terminationStatus == 0 {
          continuation.resume(returning: out)
        } else {
          continuation.resume(throwing: ServiceError.commandFailed(
            exit: p.terminationStatus, stderr: err))
        }
      }

      do {
        try process.run()
      } catch {
        continuation.resume(throwing: error)
      }
    }
  }
}
