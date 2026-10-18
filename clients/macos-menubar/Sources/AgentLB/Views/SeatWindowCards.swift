import SwiftUI

struct SeatWindowCard: View {
  let model: SeatWindowCardModel

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text(model.title)
          .font(.system(size: 11, weight: .semibold))
          .tracking(0.6)
          .foregroundStyle(.secondary)
          .lineLimit(1)
        Spacer(minLength: 4)
        Text(model.seatsText)
          .font(.system(size: 9, design: .monospaced))
          .monospacedDigit()
          .foregroundStyle(.tertiary)
          .lineLimit(1)
          .fixedSize()
      }
      HStack(alignment: .center, spacing: 9) {
        RingGauge(percent: model.remainingPercent, lineWidth: 3)
          .frame(width: 30, height: 30)
        Text(model.valueText)
          .font(.system(size: 26, weight: model.remainingPercent < 15 ? .bold : .semibold, design: .monospaced))
          .monospacedDigit()
          .lineLimit(1)
          .minimumScaleFactor(0.8)
      }
      VStack(alignment: .leading, spacing: 3) {
        if let detail = model.detail {
          Text(detail)
            .font(.system(size: 11))
            .foregroundStyle(.secondary)
            .lineLimit(1)
            .truncationMode(.tail)
        }
        Text(model.resetText)
          .font(.system(size: 10, design: .monospaced))
          .monospacedDigit()
          .foregroundStyle(.secondary)
          .lineLimit(1)
          .minimumScaleFactor(0.85)
        Text(model.sourceText)
          .font(.system(size: 10, design: .monospaced))
          .monospacedDigit()
          .foregroundStyle(.tertiary)
          .lineLimit(1)
          .minimumScaleFactor(0.85)
      }
    }
    .padding(12)
    .frame(maxWidth: .infinity, alignment: .leading)
    .frame(height: PanelMetrics.poolCard, alignment: .top)
    .background(.thinMaterial, in: .rect(cornerRadius: 8))
    .help(model.help)
    .accessibilityElement(children: .combine)
  }
}

struct SeatQuietCard: View {
  let vendor: SeatVendorSummary

  var body: some View {
    VStack(alignment: .leading, spacing: 0) {
      HStack(alignment: .firstTextBaseline, spacing: 6) {
        Text("USAGE")
          .font(.system(size: 11, weight: .semibold))
          .tracking(0.6)
          .foregroundStyle(.secondary)
        Spacer(minLength: 4)
        Text(vendor.seatsText)
          .font(.system(size: 9, design: .monospaced))
          .monospacedDigit()
          .foregroundStyle(.tertiary)
          .lineLimit(1)
      }
      Spacer(minLength: 0)
      Text(vendor.quietLine ?? "")
        .font(.system(size: 12))
        .foregroundStyle(.secondary)
        .lineLimit(1)
        .minimumScaleFactor(0.85)
        .frame(maxWidth: .infinity, alignment: .center)
      Spacer(minLength: 0)
    }
    .padding(12)
    .frame(maxWidth: .infinity, alignment: .leading)
    .frame(height: PanelMetrics.poolCard, alignment: .top)
    .background(.thinMaterial, in: .rect(cornerRadius: 8))
    .accessibilityElement(children: .combine)
  }
}
