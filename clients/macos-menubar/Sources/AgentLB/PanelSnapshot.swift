import AppKit
import SwiftUI

@MainActor
enum PanelSnapshot {
  static func runIfRequested() {
    let args = CommandLine.arguments
    guard let flag = args.firstIndex(of: "--snapshot"), flag + 1 < args.count else { return }
    let out = URL(fileURLWithPath: args[flag + 1])
    let scopeName = args.firstIndex(of: "--scope").flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil }
    let scope = scopeName.flatMap(ProviderScope.init(rawValue:)) ?? .all
    let dark = args.contains("--dark")
    _ = NSApplication.shared
    NSApp.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
    let state = AppState()
    let progress = Progress(totalUnitCount: 1)
    Task { @MainActor in
      state.providerScope = scope
      await state.refreshNow()
      await state.refreshNow()
      progress.completedUnitCount = 1
    }
    let deadline = Date().addingTimeInterval(60)
    while !progress.isFinished && Date() < deadline {
      RunLoop.main.run(until: Date().addingTimeInterval(0.1))
    }
    exit(render(state: state, scope: scope, dark: dark, out: out) ? 0 : 2)
  }

  private static func render(state: AppState, scope: ProviderScope, dark: Bool, out: URL) -> Bool {
    guard let defaults = UserDefaults(suiteName: "com.aneyman.agentlb.snapshot") else { return false }
    defaults.set(scope.rawValue, forKey: "providerScope")
    defaults.set(false, forKey: "privacyMode")
    defaults.set(true, forKey: "recentExpanded")
    let view = RootView()
      .environment(state)
      .defaultAppStorage(defaults)
      .environment(\.colorScheme, dark ? .dark : .light)
      .frame(width: PanelMetrics.width)
      .background(Color(nsColor: dark ? .black : .windowBackgroundColor))
    let renderer = ImageRenderer(content: view)
    renderer.scale = 2
    guard let image = renderer.cgImage else { return false }
    let rep = NSBitmapImageRep(cgImage: image)
    guard let png = rep.representation(using: .png, properties: [:]) else { return false }
    do {
      try png.write(to: out)
    } catch {
      return false
    }
    FileHandle.standardOutput.write(Data("snapshot \(out.path) scope=\(scope.rawValue) status=\(state.serviceStatus)\n".utf8))
    return true
  }
}
