import Darwin
import Foundation

/// Ensures at most one AgentLB menubar process runs per user session per
/// machine, using an `flock` on a file under Application Support. A launch
/// from a second bundle copy (e.g. ~/Applications vs. the repo bundle) must
/// exit rather than register a second status item.
enum SingleInstanceGuard {
  // Held for the process lifetime once acquired; access is confined to the
  // sequential acquire/release calls made from app startup and tests.
  nonisolated(unsafe) private static var lockFileDescriptor: Int32 = -1

  /// Attempts to take an exclusive, non-blocking lock on `lockURL`. Retries
  /// on `EWOULDBLOCK` (lock currently held) up to `attempts` times, sleeping
  /// `retryDelay` between tries — this absorbs the brief overlap during
  /// `launchctl kickstart -k` restarts. Returns `false` once attempts are
  /// exhausted or the lock file cannot be opened at all.
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

  /// `~/Library/Application Support/AgentLB/menubar.lock`, creating the
  /// directory if needed.
  static func defaultLockURL() -> URL {
    let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
      ?? FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support", isDirectory: true)
    let directory = appSupport.appendingPathComponent("AgentLB", isDirectory: true)
    try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    return directory.appendingPathComponent("menubar.lock")
  }

  /// Test hook: releases the held lock and closes the retained descriptor so
  /// a subsequent `acquire` in the same process can succeed again.
  static func releaseForTesting() {
    guard lockFileDescriptor >= 0 else { return }
    flock(lockFileDescriptor, LOCK_UN)
    close(lockFileDescriptor)
    lockFileDescriptor = -1
  }
}
