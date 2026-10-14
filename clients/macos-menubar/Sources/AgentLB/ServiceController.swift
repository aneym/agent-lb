import Foundation
import Darwin



enum ServiceError: Error, Sendable {
  case commandFailed(exit: Int32, stderr: String)
}








struct ServiceController: Sendable {

  static let label = "com.aneyman.agent-lb"

  static var plistPath: String {
    (NSString("~/Library/LaunchAgents/com.aneyman.agent-lb.plist")
      as NSString).expandingTildeInPath
  }

  static var domainTarget: String {
    "gui/\(getuid())/\(label)"
  }



  static func statusCommand() -> [String] {
    ["launchctl", "list", label]
  }



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






  static func parsePIDColumn(_ line: String) -> Bool {
    let parts = line.split(separator: "\t", omittingEmptySubsequences: false)
    guard let first = parts.first else { return false }
    let pid = String(first)
    return pid != "-" && Int(pid) != nil
  }



  func isLoadedWithPID() async -> Bool {
    guard let output = try? await run(ServiceController.statusCommand()) else {
      return false
    }

    for line in output.split(separator: "\n", omittingEmptySubsequences: true) {
      if ServiceController.parsePIDColumn(String(line)) { return true }
    }
    return false
  }





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
