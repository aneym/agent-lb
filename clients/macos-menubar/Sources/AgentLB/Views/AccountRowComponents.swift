import SwiftUI

enum RowPresentation: Equatable, Sendable {
  case active
  case rateLimited(Date?)
  case paused
  case reconnecting
  case deactivated(reauth: Bool)
  case unsubscribed

  static func forSeat(_ seat: SeatAccount) -> RowPresentation {
    switch seat.displayStatus {
    case "rate_limited": .rateLimited(seat.displayStatusResetAt)
    case "paused": .paused
    case "exchange_uncertain": .reconnecting
    case "reauth_required": .deactivated(reauth: true)
    default: .active
    }
  }

  var isDimmed: Bool {
    switch self {
    case .paused, .unsubscribed: true
    default: false
    }
  }

  func label(now: Date, activeText: String?, reset: (Date) -> String) -> String? {
    switch self {
    case .active:
      return activeText
    case .rateLimited(let resetAt):
      guard let resetAt, resetAt > now else { return "limited" }
      return "limited · \(reset(resetAt))"
    case .paused:
      return "paused"
    case .reconnecting:
      return "reconnecting"
    case .deactivated(let reauth):
      return reauth ? "re-auth needed" : "inactive"
    case .unsubscribed:
      return "unsubscribed"
    }
  }
}

struct RowRing: View {
  let percent: Double?
  let presentation: RowPresentation
  var pending = false

  var drawsGauge: Bool { percent != nil && !pending }

  var body: some View {
    if pending {
      ProgressView()
        .controlSize(.small)
        .frame(width: 20, height: 20)
    } else {
      ZStack {
        if drawsGauge {
          RingGauge(percent: percent, lineWidth: 2)
        } else {
          Circle().stroke(.quaternary, style: StrokeStyle(lineWidth: 2, dash: [2, 3]))
        }
        switch presentation {
        case .active, .rateLimited, .unsubscribed, .reconnecting:
          EmptyView()
        case .paused:
          StatusGlyph(kind: .paused, size: 8)
        case .deactivated:
          StatusGlyph(kind: .deactivated, size: 8)
        }
      }
      .frame(width: 20, height: 20)
    }
  }
}

struct PlanChip: View {
  let text: String?

  var body: some View {
    if let text, !text.isEmpty {
      Text(text.uppercased())
        .font(.system(size: 9, weight: .medium))
        .tracking(0.5)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 5)
        .padding(.vertical, 1)
        .background(Capsule().fill(.quaternary.opacity(0.5)))
        .lineLimit(1)
        .fixedSize()
    }
  }
}

struct RowTrailing: View {
  let presentation: RowPresentation
  let text: String?

  var body: some View {
    if let text {
      if case .rateLimited = presentation {
        Text(text)
          .font(.system(size: 12, weight: .bold, design: .monospaced))
          .monospacedDigit()
          .lineLimit(1)
          .fixedSize()
      } else {
        Text(text)
          .font(.system(size: 12))
          .monospacedDigit()
          .foregroundStyle(.secondary)
          .lineLimit(1)
          .fixedSize()
      }
    }
  }
}

struct RowWindow: Equatable, Identifiable, Sendable {
  let id: String
  let label: String
  let percent: Double?
  var knownFull = false
  let resetAt: Date?
  var help: String? = nil
}

struct RowWindowGrid: View {
  let cells: [RowWindow]
  let now: Date
  var emptyText: String? = nil

  var body: some View {
    HStack(alignment: .center, spacing: 12) {
      if cells.isEmpty, let emptyText {
        Text(emptyText)
          .font(.system(size: 10, design: .monospaced))
          .foregroundStyle(.tertiary)
          .lineLimit(1)
          .frame(maxWidth: .infinity, alignment: .leading)
      } else {
        ForEach(cells) { cell in
          WindowCell(cell: cell, labelWidth: labelWidth, now: now)
        }
      }
    }
  }

  private var labelWidth: CGFloat {
    let longest = cells.map(\.label.count).max() ?? 2
    return max(16, CGFloat(longest) * 7.5)
  }
}

private struct WindowCell: View {
  let cell: RowWindow
  let labelWidth: CGFloat
  let now: Date

  var body: some View {
    HStack(spacing: 5) {
      Text(cell.label)
        .font(.system(size: 9, weight: .semibold))
        .tracking(0.5)
        .foregroundStyle(.secondary)
        .lineLimit(1)
        .frame(width: labelWidth, alignment: .leading)
      if cell.percent == nil && cell.resetAt == nil {
        Text("—")
          .font(.system(size: 10, design: .monospaced))
          .foregroundStyle(.tertiary)
      } else {
        MonoMeter(percent: cell.percent)
          .frame(width: 56)
        Text(detail)
          .font(.system(size: 10, design: .monospaced))
          .monospacedDigit()
          .foregroundStyle(.secondary)
          .lineLimit(1)
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .help(cell.help ?? "")
  }

  private var detail: String {
    var parts: [String] = []
    if let percent = cell.percent { parts.append(Format.percent(percent)) }
    if !cell.knownFull, let resetAt = cell.resetAt, resetAt > now {
      parts.append(Format.countdownCompact(to: resetAt, relativeTo: now))
    }
    return parts.isEmpty ? "—" : parts.joined(separator: " · ")
  }
}
