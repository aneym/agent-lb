import SwiftUI

struct GoogleUsageStrip: View {
  let usage: GoogleUsage?

  var body: some View {
    VStack(alignment: .leading, spacing: PanelMetrics.metricsSpacing) {
      HStack(spacing: 14) {
        Text(usage.map { "\(Format.compact($0.requests7d)) req · 7d" } ?? "— req · 7d")
        if let usage {
          Text("\(Format.compact(usage.requests5h)) req · 5h")
        }
      }
      .frame(height: PanelMetrics.metricsLine)
      HStack(spacing: 14) {
        Text(usage.map { "\(Format.compactLarge($0.tokens7d)) tok · 7d" } ?? "— tok · 7d")
        if let plan = usage?.plan {
          Text(plan)
            .font(.system(size: 9))
            .foregroundStyle(.tertiary)
        }
      }
      .frame(height: PanelMetrics.metricsLine)
      ForEach(usage?.activeSources ?? []) { source in
        HStack(spacing: 8) {
          Text(source.label)
            .truncationMode(.tail)
          Spacer(minLength: 6)
          Text(source.figures)
            .layoutPriority(1)
        }
        .frame(height: PanelMetrics.metricsLine)
        .help(source.help)
      }
    }
    .font(.system(size: 11, design: .monospaced))
    .monospacedDigit()
    .foregroundStyle(.secondary)
    .lineLimit(1)
    .help("Requests and tokens on the Google sign-in: the Neon seat's Antigravity CLI plus agent-lb gateway keys.")
  }
}
