import Darwin
import Foundation





enum SingleInstanceGuard {


  nonisolated(unsafe) private static var lockFileDescriptor: Int32 = -1






  @discardableResult
  static func acquire(lockURL: URL, attempts: Int = 10, retryDelay: TimeInterval = 0.5) -> Bool {
    let directory = lockURL.deletingLastPathComponent()
    try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

    let path = lockURL.path
    let maxAttempts = max(attempts, 1)
    for attempt in 0..<maxAttempts {
      let fd = open(path, O_CREAT | O_RDWR, 0o644)
      guard fd >= 0 else { return false }

      if flock(fd, LOCK_EX | LOCK_NB) == 0 {
        lockFileDescriptor = fd
        return true
      }

      let lockError = errno
      close(fd)
      guard lockError == EWOULDBLOCK, attempt < maxAttempts - 1 else { return false }
      Thread.sleep(forTimeInterval: retryDelay)
    }
    return false
  }



  static func defaultLockURL() -> URL {
    let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
      ?? FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support", isDirectory: true)
    let directory = appSupport.appendingPathComponent("AgentLB", isDirectory: true)
    try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    return directory.appendingPathComponent("menubar.lock")
  }



  static func releaseForTesting() {
    guard lockFileDescriptor >= 0 else { return }
    flock(lockFileDescriptor, LOCK_UN)
    close(lockFileDescriptor)
    lockFileDescriptor = -1
  }
}
