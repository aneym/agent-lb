import AppKit
import SwiftUI

struct FooterBar: View {
  @Environment(AppState.self) private var appState
  @Environment(\.dismiss) private var dismiss
  @State private var copied = false
  @State private var showStopConfirm = false

  private var serviceDown: Bool {
    appState.serviceStatus == .stopped || appState.serviceStatus == .unreachable
  }

  var body: some View {
    GlassEffectContainer {
      HStack(spacing: 8) {
        Button("Dashboard") {
          NSWorkspace.shared.open(appState.dashboardURL)
          dismiss()
        }
        .buttonStyle(.monochromeProminent)
        .disabled(serviceDown)

        Button(copied ? "Copied" : "Copy URL") {
          NSPasteboard.general.clearContents()
          NSPasteboard.general.setString(appState.baseURL.absoluteString, forType: .string)
          copied = true
          Task {
            try? await Task.sleep(for: .milliseconds(1200))
            copied = false
          }
        }
        .buttonStyle(.glass)

        Spacer(minLength: 0)

        if !appState.isRemote {
          Button {
            Task { await appState.restartService() }
          } label: {
            if appState.isRestarting {
              ProgressView()
                .controlSize(.mini)
            } else {
              Text("Restart")
            }
          }
          .buttonStyle(.glass)
          .disabled(serviceDown || appState.isRestarting)

          powerMenu
        }
      }
      // PanelMetrics.footerControls — enforced so PanelLayout stays exact.
      .frame(height: PanelMetrics.footerControls)
      .padding(.horizontal, 14)
      .padding(.vertical, 12)
    }
    // §8.2: footer buttons step up to .regular for the larger panel.
    .controlSize(.regular)
    .confirmationDialog("Stop Agent LB?", isPresented: $showStopConfirm) {
      Button("Stop Anyway", role: .destructive) {
        Task { await appState.stopService() }
      }
    } message: {
      Text("The watchdog may restart the service. Stop anyway?")
    }
  }

  private var powerMenu: some View {
    Menu {
      if serviceDown {
        Button("Start Service") { Task { await appState.startService() } }
      } else {
        Button("Stop Service") { showStopConfirm = true }
      }
      Divider()
      Button("Quit Agent LB") {
        dismiss()
        NSApplication.shared.terminate(nil)
      }
    } label: {
      Image(systemName: "power")
        .frame(width: 22, height: 22)
        .contentShape(Rectangle())
    }
    .menuStyle(.button)
    .buttonStyle(.plain)
    .accessibilityLabel("Service power options")
  }
}

// MARK: - Shared components

// macOS 26 `MenuBarExtra(.window)` measures its panel once (first layout
// after open) and never re-measures when the SwiftUI content's ideal size
// changes. The panel height is therefore a pure function of app state
// (PanelLayout); RootView pins its content to that height and calls `apply`
// so the NSWindow always matches. No geometry feedback anywhere.
@MainActor
enum PanelResizer {
  /// Set the panel window to the PanelLayout-computed height (top-anchored).
  static func apply(_ height: Double) {
    let target = min(height, PanelMetrics.maxHeight)
    guard target > 60, let panel = visiblePanel() else { return }
    let current = panel.frame.height
    guard abs(target - current) > 0.5 else { return }
    var frame = panel.frame
    // AppKit origin is bottom-left; keep the top edge anchored to the bar.
    frame.origin.y += current - target
    frame.size.height = target
    panel.setFrame(frame, display: true)
  }

  private static func visiblePanel() -> NSWindow? {
    NSApp.windows.first {
      $0.isVisible && String(describing: type(of: $0)).contains("MenuBarExtraWindow")
    }
  }
}

// §2.3: zero hue anywhere. `.glassProminent` composites with the system
// accent on macOS 26 (and fighting it with `.tint(.primary)` produced an
// illegible washed-out pill on some machines), so prominent actions use a
// fully-owned monochrome style: ink capsule (`.primary` — white in dark
// mode, black in light mode) with the label inverted via `.background`
// (black text in dark, white text in light). The label style lives inside
// makeBody so nothing downstream can override it.
struct MonochromeProminentButtonStyle: ButtonStyle {
  @Environment(\.isEnabled) private var isEnabled

  func makeBody(configuration: Configuration) -> some View {
    configuration.label
      .font(.system(size: 13, weight: .medium))
      .foregroundStyle(.background)
      .lineLimit(1)
      .padding(.horizontal, 14)
      .padding(.vertical, 5)
      .background(Capsule().fill(.primary))
      .opacity(configuration.isPressed ? 0.85 : (isEnabled ? 1 : 0.5))
  }
}

extension ButtonStyle where Self == MonochromeProminentButtonStyle {
  static var monochromeProminent: MonochromeProminentButtonStyle { .init() }
}

struct SectionLabel: View {
  let text: String

  init(_ text: String) {
    self.text = text
  }

  var body: some View {
    Text(text.uppercased())
      .font(.system(size: 11, weight: .semibold))
      .tracking(0.6)
      .foregroundStyle(.secondary)
  }
}

/// The dashboard's monochrome quota bar (§2.4) — back in service for the
/// §9.3 account-row window grid. `Capsule` track in `.quaternary`, fill in
/// `.primary`, 4 pt tall; nil percent renders the bare track.
struct MonoMeter: View {
  let percent: Double?

  var body: some View {
    GeometryReader { geometry in
      ZStack(alignment: .leading) {
        Capsule().fill(.quaternary)
        if let percent {
          Capsule()
            .fill(.primary)
            .frame(width: geometry.size.width * (min(max(percent, 0), 100) / 100))
        }
      }
    }
    .frame(height: 4)
    .animation(.easeOut(duration: 0.15), value: percent)
  }
}

/// §8 monochrome ring gauge — pool cards (30 pt) and account rows (20 pt).
/// Track `.quaternary`, fill `.primary`, rounded caps, 12 o'clock clockwise.
/// `percent == nil` renders the track only.
struct RingGauge: View {
  let percent: Double?
  var lineWidth: CGFloat = 2

  var body: some View {
    ZStack {
      Circle()
        .stroke(.quaternary, lineWidth: lineWidth)
      Circle()
        .trim(from: 0, to: fraction)
        .stroke(.primary, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
        .rotationEffect(.degrees(-90))
    }
    // Strokes straddle the path; inset so caps never clip at the frame edge.
    .padding(lineWidth / 2)
    .animation(.easeOut(duration: 0.15), value: percent)
  }

  private var fraction: CGFloat {
    CGFloat(min(max(percent ?? 0, 0), 100) / 100)
  }
}

struct StatusGlyph: View {
  enum Kind {
    case active, rateLimited, paused, deactivated
  }

  let kind: Kind
  var size: CGFloat = 11

  var body: some View {
    Image(systemName: symbol)
      .font(.system(size: size, weight: kind == .deactivated ? .bold : .regular))
      .foregroundStyle(kind == .active ? AnyShapeStyle(.primary) : AnyShapeStyle(.secondary))
  }

  private var symbol: String {
    switch kind {
    case .active: "circle.fill"
    case .rateLimited: "circle.lefthalf.filled"
    case .paused: "pause"
    case .deactivated: "xmark"
    }
  }
}

struct CountdownText: View {
  let target: Date
  var prefix: String = ""

  var body: some View {
    // Sub-hour countdowns tick every second; above an hour the rendered
    // string only changes per minute, so a 60 s cadence avoids re-renders.
    let cadence: TimeInterval = target.timeIntervalSinceNow > 3600 ? 60 : 1
    TimelineView(.periodic(from: .now, by: cadence)) { _ in
      Text(prefix + Format.countdown(to: target))
        .monospacedDigit()
    }
  }
}

struct RetryRow: View {
  let retry: () -> Void

  var body: some View {
    Button(action: retry) {
      Text("couldn't load — retry")
        .font(.system(size: 11))
        .underline()
        .foregroundStyle(.secondary)
    }
    .buttonStyle(.plain)
    // PanelMetrics.retryRow — enforced so PanelLayout stays exact.
    .frame(height: PanelMetrics.retryRow)
  }
}

// MARK: - Status-bar ring gauge (§8.1)

enum StatusIconRenderer {
  enum IconState: Hashable, Sendable {
    case healthy, risk, down, update
  }

  /// Cache key: state + all percents bucketed to 4 % steps (§8.1) so
  /// closed-state 120 s polling can only ever materialize a bounded set of
  /// composites per state.
  private struct Key: Hashable {
    let state: IconState
    let outerBucket: Int?
    let innerBucket: Int?
    let showFable: Bool
    let fableBucket: Int?
  }

  @MainActor private static var cache: [Key: NSImage] = [:]

  private static let cellSize: CGFloat = 18
  private static let fableGap: CGFloat = 2

  /// Outer ring = tactical 5-hour (primary) remaining %; inner ring =
  /// strategic weekly/monthly (long-window) remaining %. Both follow the
  /// current provider scope — AppState feeds the already-scoped percents in.
  /// `showFable` appends the Claude-focus cell: a third circle showing pool
  /// Fable remaining % (track-only when the pool has no scoped data yet).
  @MainActor
  static func icon(
    for state: IconState,
    primaryPercent: Double? = nil,
    longWindowPercent: Double? = nil,
    showFable: Bool = false,
    fablePercent: Double? = nil
  ) -> NSImage {
    let outerBucket = primaryPercent.map { Int(min(max($0, 0), 100) / 4) }
    let innerBucket = longWindowPercent.map { Int(min(max($0, 0), 100) / 4) }
    let fableBucket = showFable ? fablePercent.map { Int(min(max($0, 0), 100) / 4) } : nil
    let key = Key(
      state: state, outerBucket: outerBucket, innerBucket: innerBucket,
      showFable: showFable, fableBucket: fableBucket
    )
    if let cached = cache[key] { return cached }
    let image = render(
      state: state, outerBucket: outerBucket, innerBucket: innerBucket,
      showFable: showFable, fableBucket: fableBucket
    )
    cache[key] = image
    return image
  }

  private static func render(
    state: IconState,
    outerBucket: Int?,
    innerBucket: Int?,
    showFable: Bool,
    fableBucket: Int?
  ) -> NSImage {
    let width = showFable ? cellSize * 2 + fableGap : cellSize
    let image = NSImage(size: NSSize(width: width, height: cellSize), flipped: false) { _ in
      // Existing two-ring composite always occupies the left 18 pt cell so
      // the icon reads unchanged when the Fable cell toggles on/off.
      let rect = NSRect(x: 0, y: 0, width: cellSize, height: cellSize)
      let stroke: CGFloat = 2.0
      let ringAlpha: CGFloat = state == .down ? 0.35 : 1
      let center = NSPoint(x: rect.midX, y: rect.midY)
      // Stroke straddles the path: radius leaves stroke/2 + a hair of margin.
      let outerRadius = rect.width / 2 - 1.0 - 0.5
      let innerRadius = outerRadius - 3.0

      drawRing(center: center, radius: outerRadius, stroke: stroke, bucket: outerBucket, ringAlpha: ringAlpha)
      // .risk punches the exclamation glyph through the icon center, which
      // the inner ring would otherwise occupy — draw only the outer ring so
      // the glyph reads clearly (trades away the long-window ring for legibility).
      if state != .risk {
        drawRing(center: center, radius: innerRadius, stroke: stroke, bucket: innerBucket, ringAlpha: ringAlpha)
      }

      switch state {
      case .healthy:
        break
      case .risk:
        // Bold exclamation centered in the ring.
        drawSymbol("exclamationmark", pointSize: 7, weight: .bold, centeredIn: rect)
      case .down:
        // Diagonal slash through the dimmed rings, full alpha.
        let slash = NSBezierPath()
        slash.move(to: NSPoint(x: rect.minX + 3.5, y: rect.minY + 3.5))
        slash.line(to: NSPoint(x: rect.maxX - 3.5, y: rect.maxY - 3.5))
        slash.lineWidth = 1.5
        slash.lineCapStyle = .round
        NSColor.black.setStroke()
        slash.stroke()
      case .update:
        // 3 pt dot at top-right inside a punched-hole halo.
        let dot = NSRect(x: rect.maxX - 4, y: rect.maxY - 4, width: 3, height: 3)
        punchHole(dot.insetBy(dx: -1, dy: -1))
        NSColor.black.setFill()
        NSBezierPath(ovalIn: dot).fill()
      }

      if showFable {
        drawFableCell(
          in: NSRect(x: cellSize + fableGap, y: 0, width: cellSize, height: cellSize),
          stroke: stroke,
          bucket: fableBucket,
          ringAlpha: ringAlpha
        )
      }
      return true
    }
    // Template rendering: the menu bar tints the composite; we only draw alpha.
    image.isTemplate = true
    return image
  }

  /// One ring: full-circle track at 22 % alpha (further dimmed when down) +
  /// a remaining-percent arc from 12 o'clock (90°) clockwise, rounded caps,
  /// full alpha. Unknown percent (nil bucket) → track only.
  private static func drawRing(
    center: NSPoint,
    radius: CGFloat,
    stroke: CGFloat,
    bucket: Int?,
    ringAlpha: CGFloat
  ) {
    let track = NSBezierPath()
    track.appendArc(withCenter: center, radius: radius, startAngle: 0, endAngle: 360)
    track.lineWidth = stroke
    NSColor.black.withAlphaComponent(0.22 * ringAlpha).setStroke()
    track.stroke()

    guard let bucket, bucket > 0 else { return }
    let fraction = min(Double(bucket) * 4 / 100, 1)
    let arc = NSBezierPath()
    arc.appendArc(
      withCenter: center,
      radius: radius,
      startAngle: 90,
      endAngle: 90 - 360 * fraction,
      clockwise: true
    )
    arc.lineWidth = stroke
    arc.lineCapStyle = .round
    NSColor.black.withAlphaComponent(ringAlpha).setStroke()
    arc.stroke()
  }

  /// Claude-focus Fable cell: one ring at the outer radius showing pool Fable
  /// remaining %, with a small F glyph centered so the extra circle reads as
  /// the Fable gauge rather than a third usage window.
  private static func drawFableCell(
    in rect: NSRect,
    stroke: CGFloat,
    bucket: Int?,
    ringAlpha: CGFloat
  ) {
    let center = NSPoint(x: rect.midX, y: rect.midY)
    let radius = rect.width / 2 - 1.0 - 0.5
    drawRing(center: center, radius: radius, stroke: stroke, bucket: bucket, ringAlpha: ringAlpha)
    let glyph = NSAttributedString(
      string: "F",
      attributes: [
        .font: NSFont.systemFont(ofSize: 8, weight: .bold),
        .foregroundColor: NSColor.black.withAlphaComponent(ringAlpha),
      ]
    )
    let size = glyph.size()
    glyph.draw(at: NSPoint(x: rect.midX - size.width / 2, y: rect.midY - size.height / 2))
  }

  private static func drawSymbol(
    _ name: String,
    pointSize: CGFloat,
    weight: NSFont.Weight,
    centeredIn rect: NSRect,
    alpha: CGFloat = 1
  ) {
    let configuration = NSImage.SymbolConfiguration(pointSize: pointSize, weight: weight)
    guard
      let image = NSImage(systemSymbolName: name, accessibilityDescription: nil)?
        .withSymbolConfiguration(configuration)
    else { return }
    let origin = NSPoint(
      x: rect.midX - image.size.width / 2,
      y: rect.midY - image.size.height / 2
    )
    image.draw(
      in: NSRect(origin: origin, size: image.size),
      from: .zero,
      operation: .sourceOver,
      fraction: alpha
    )
  }

  private static func punchHole(_ rect: NSRect) {
    guard let context = NSGraphicsContext.current else { return }
    let previous = context.compositingOperation
    context.compositingOperation = .destinationOut
    NSColor.black.setFill()
    NSBezierPath(ovalIn: rect).fill()
    context.compositingOperation = previous
  }
}
