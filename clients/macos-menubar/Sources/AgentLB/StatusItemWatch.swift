import AppKit
import Foundation










enum StatusItemWatch {
  static let exitCode: Int32 = 3
  static let maxConsecutiveExits = 5
  static let checkDelays: [Duration] = [.seconds(15), .seconds(45)]

  static var counterURL: URL {
    SingleInstanceGuard.defaultLockURL()
      .deletingLastPathComponent()
      .appendingPathComponent("status-item-restarts")
  }

  @MainActor
  static func schedule() {
    Task { @MainActor in
      for delay in checkDelays {
        try? await Task.sleep(for: delay)
        if hasStatusItemWindow(NSApp.windows) {
          writeCounter(0)
          return
        }
      }
      let exits = readCounter() + 1
      writeCounter(exits)
      let note: String
      if exits > maxConsecutiveExits {
        note = "AgentLB: status item missing after \(exits - 1) relaunches; staying up.\n"
        FileHandle.standardError.write(Data(note.utf8))
        return
      }
      note = "AgentLB: status item never registered; exiting \(exitCode) for launchd relaunch (\(exits)/\(maxConsecutiveExits)).\n"
      FileHandle.standardError.write(Data(note.utf8))
      exit(exitCode)
    }
  }


  nonisolated static func hasStatusItemWindow(_ windows: [NSWindow]) -> Bool {
    windows.contains { String(describing: type(of: $0)).contains("StatusBarWindow") }
  }

  nonisolated static func readCounter(at url: URL? = nil) -> Int {
    let url = url ?? counterURL
    guard let text = try? String(contentsOf: url, encoding: .utf8) else { return 0 }
    return Int(text.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
  }

  nonisolated static func writeCounter(_ value: Int, at url: URL? = nil) {
    let url = url ?? counterURL
    try? String(value).write(to: url, atomically: true, encoding: .utf8)
  }
}
