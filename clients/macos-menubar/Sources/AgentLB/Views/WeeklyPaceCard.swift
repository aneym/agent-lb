import SwiftUI

struct WeeklyPaceCard: View {
  let rows: [WeeklyPaceRow]
  let showsNames: Bool
  let now: Date

  var body: some View {
    VStack(alignment: .leading, spacing: PanelMetrics.paceHeaderGap) {
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text("WEEKLY PACE")
          .font(.system(size: 11, weight: .semibold))
          .tracking(0.6)
          .foregroundStyle(.secondary)
        Spacer(minLength: 4)
        Text("burn ÷ sustainable")
          .font(.system(size: 9, design: .monospaced))
          .foregroundStyle(.tertiary)
      }
      .lineLimit(1)
      .frame(height: PanelMetrics.paceHeader)
      VStack(spacing: PanelMetrics.paceRowSpacing) {
        ForEach(rows) { row in
          PaceRowView(row: row, showsName: showsNames, now: now)
            .frame(height: PanelMetrics.paceRow)
        }
      }
    }
    .padding(.horizontal, 12)
    .padding(.vertical, PanelMetrics.pacePadding)
    .frame(maxWidth: .infinity, alignment: .leading)
    .frame(height: PanelMetrics.paceCard(rows: rows.count))
    .background(.thinMaterial, in: .rect(cornerRadius: 8))
  }
}

private struct PaceRowView: View {
  let row: WeeklyPaceRow
  let showsName: Bool
  let now: Date

  var body: some View {
    HStack(spacing: 10) {
      if showsName {
        Text(row.name)
          .font(.system(size: 11))
          .foregroundStyle(.secondary)
          .frame(width: 44, alignment: .leading)
      }
      PaceTrack(multiple: row.multiple, over: row.isOver)
      Text(row.multipleText)
        .font(.system(size: 12, weight: row.isOver ? .bold : .semibold, design: .monospaced))
        .frame(width: 42, alignment: .trailing)
      Text(row.outlook(now: now))
        .font(.system(size: 10, weight: row.dryAt == nil ? .regular : .semibold, design: .monospaced))
        .foregroundStyle(row.dryAt == nil ? AnyShapeStyle(.secondary) : AnyShapeStyle(.primary))
        .frame(width: 86, alignment: .leading)
    }
    .monospacedDigit()
    .lineLimit(1)
    .opacity(row.partial ? 0.5 : 1)
    .help(row.help)
    .accessibilityElement(children: .combine)
  }
}

private struct PaceTrack: View {
  let multiple: Double?
  let over: Bool

  var body: some View {
    GeometryReader { geo in
      let width = geo.size.width
      let mid = geo.size.height / 2
      ZStack(alignment: .leading) {
        Capsule()
          .fill(.quaternary)
          .frame(height: 3)
        Rectangle()
          .fill(.secondary)
          .frame(width: 1, height: 9)
          .offset(x: width / 2 - 0.5)
        if let multiple, multiple >= 0.05 {
          let x = WeeklyPaceRow.position(multiple) * width
          Capsule()
            .fill(over ? AnyShapeStyle(.primary) : AnyShapeStyle(.secondary))
            .frame(width: abs(x - width / 2), height: 3)
            .offset(x: min(x, width / 2))
          Circle()
            .fill(over ? AnyShapeStyle(.primary) : AnyShapeStyle(.secondary))
            .frame(width: 7, height: 7)
            .offset(x: min(max(x - 3.5, 0), width - 7))
        }
      }
      .frame(width: width, height: geo.size.height)
      .position(x: width / 2, y: mid)
    }
    .animation(.easeOut(duration: 0.2), value: multiple)
  }
}
