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

      .frame(height: PanelMetrics.footerControls)
      .padding(.horizontal, 14)
      .padding(.vertical, 12)
    }

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








@MainActor
enum PanelResizer {

  static func apply(_ height: Double) {
    let target = min(height, PanelMetrics.maxHeight)
    guard target > 60, let panel = visiblePanel() else { return }
    let current = panel.frame.height
    guard abs(target - current) > 0.5 else { return }
    var frame = panel.frame

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

    .frame(height: PanelMetrics.retryRow)
  }
}



enum StatusIconRenderer {
  enum IconState: Hashable, Sendable {
    case healthy, risk, down, update
  }




  private struct Key: Hashable {
    let state: IconState
    let outerBucket: Int?
    let innerBucket: Int?
    let showFable: Bool
    let fableBucket: Int?
    let paceText: String?
  }

  @MainActor private static var cache: [Key: NSImage] = [:]

  private static let cellSize: CGFloat = 18
  private static let fableGap: CGFloat = 2
  private static let paceGap: CGFloat = 3






  @MainActor
  static func icon(
    for state: IconState,
    primaryPercent: Double? = nil,
    longWindowPercent: Double? = nil,
    showFable: Bool = false,
    fablePercent: Double? = nil,
    paceText: String? = nil
  ) -> NSImage {
    let outerBucket = primaryPercent.map { Int(min(max($0, 0), 100) / 4) }
    let innerBucket = longWindowPercent.map { Int(min(max($0, 0), 100) / 4) }
    let fableBucket = showFable ? fablePercent.map { Int(min(max($0, 0), 100) / 4) } : nil
    let key = Key(
      state: state, outerBucket: outerBucket, innerBucket: innerBucket,
      showFable: showFable, fableBucket: fableBucket, paceText: paceText
    )
    if let cached = cache[key] { return cached }
    let image = render(
      state: state, outerBucket: outerBucket, innerBucket: innerBucket,
      showFable: showFable, fableBucket: fableBucket, paceText: paceText
    )
    cache[key] = image
    return image
  }

  private static func render(
    state: IconState,
    outerBucket: Int?,
    innerBucket: Int?,
    showFable: Bool,
    fableBucket: Int?,
    paceText: String?
  ) -> NSImage {
    let pace = paceText.map(paceGlyph)
    let cellsWidth = showFable ? cellSize * 2 + fableGap : cellSize
    let width = cellsWidth + (pace.map { paceGap + ceil($0.size().width) } ?? 0)
    let image = NSImage(size: NSSize(width: width, height: cellSize), flipped: false) { _ in


      let rect = NSRect(x: 0, y: 0, width: cellSize, height: cellSize)
      let stroke: CGFloat = 2.0
      let ringAlpha: CGFloat = state == .down ? 0.35 : 1
      let center = NSPoint(x: rect.midX, y: rect.midY)

      let outerRadius = rect.width / 2 - 1.0 - 0.5
      let innerRadius = outerRadius - 3.0

      drawRing(center: center, radius: outerRadius, stroke: stroke, bucket: outerBucket, ringAlpha: ringAlpha)



      if state != .risk {
        drawRing(center: center, radius: innerRadius, stroke: stroke, bucket: innerBucket, ringAlpha: ringAlpha)
      }

      switch state {
      case .healthy:
        break
      case .risk:

        drawSymbol("exclamationmark", pointSize: 7, weight: .bold, centeredIn: rect)
      case .down:

        let slash = NSBezierPath()
        slash.move(to: NSPoint(x: rect.minX + 3.5, y: rect.minY + 3.5))
        slash.line(to: NSPoint(x: rect.maxX - 3.5, y: rect.maxY - 3.5))
        slash.lineWidth = 1.5
        slash.lineCapStyle = .round
        NSColor.black.setStroke()
        slash.stroke()
      case .update:

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

      if let pace {
        let size = pace.size()
        pace.draw(at: NSPoint(x: cellsWidth + paceGap, y: (cellSize - size.height) / 2))
      }
      return true
    }

    image.isTemplate = true
    return image
  }




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

  private static func paceGlyph(_ text: String) -> NSAttributedString {
    NSAttributedString(
      string: text,
      attributes: [
        .font: NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .semibold),
        .foregroundColor: NSColor.black,
      ]
    )
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
