import Foundation
import ServiceManagement

// NOTE: SMAppService.mainApp requires no entitlement when the app is
// non-sandboxed.  Launch-at-login only works predictably when the app is
// run from the assembled AgentLB.app bundle (make run), not from a bare
// `swift run` binary.
enum LaunchAtLogin {
  static var isEnabled: Bool {
    SMAppService.mainApp.status == .enabled
  }

  static func set(_ enabled: Bool) throws {
    if enabled {
      try SMAppService.mainApp.register()
    } else {
      try SMAppService.mainApp.unregister()
    }
  }

  static var requiresApproval: Bool {
    SMAppService.mainApp.status == .requiresApproval
  }

  static func openSettings() {
    SMAppService.openSystemSettingsLoginItems()
  }
}
