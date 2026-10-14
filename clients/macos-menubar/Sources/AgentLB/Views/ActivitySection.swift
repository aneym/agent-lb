import SwiftUI



struct ActivitySection: View {
  let entries: [RequestLogEntry]
  let hasError: Bool
  let retry: () -> Void
  @Binding var expanded: Bool

  var body: some View {
    VStack(alignment: .leading, spacing: PanelMetrics.accountsSpacing) {
      disclosureHeader
      if expanded {
        if hasError && entries.isEmpty {
          RetryRow(retry: retry)
        } else if entries.isEmpty {
          Text("No recent requests")
            .font(.system(size: 11))
            .foregroundStyle(.secondary)

            .frame(height: PanelMetrics.recentEmptyLine)
        } else {
          card
          if hasError {
            RetryRow(retry: retry)
          }
        }
      }
    }
  }

  private var disclosureHeader: some View {
    Button {
      withAnimation(.easeOut(duration: 0.15)) { expanded.toggle() }
    } label: {
      HStack(spacing: 4) {
        SectionLabel("RECENT")
        Image(systemName: "chevron.right")
          .font(.system(size: 8, weight: .semibold))
          .foregroundStyle(.secondary)
          .rotationEffect(.degrees(expanded ? 90 : 0))
        Spacer(minLength: 0)
      }
      .contentShape(.rect)
    }
    .buttonStyle(.plain)

    .frame(height: PanelMetrics.recentHeader)
  }

  private var card: some View {


    TimelineView(.periodic(from: .now, by: 30)) { context in
      VStack(spacing: 0) {
        ForEach(entries.prefix(5)) { entry in
          ActivityRow(entry: entry, now: context.date)
        }
      }
      .padding(.vertical, 4)
      .background(.thinMaterial, in: .rect(cornerRadius: 8))
    }
  }
}

struct ActivityRow: View {
  let entry: RequestLogEntry
  var now: Date = .now

  private var isError: Bool { entry.status != "ok" }

  var body: some View {
    HStack(spacing: 6) {
      Image(systemName: isError ? "xmark" : "checkmark")
        .font(.system(size: 9, weight: isError ? .bold : .regular))
        .frame(width: 14)
      Text(entry.model ?? "—")
        .font(.system(size: 11))
        .lineLimit(1)
        .truncationMode(.middle)
      Spacer(minLength: 6)
      if isError {
        Text(entry.errorCode ?? "error")
          .font(.system(size: 11, weight: .bold, design: .monospaced))
          .lineLimit(1)
      } else {
        HStack(spacing: 8) {
          Text(entry.tokens.map(Format.tokens) ?? "—")
          Text(entry.costUsd.map { String(format: "$%.4f", $0) } ?? "—")
          Text(entry.latencyMs.map(Format.latency) ?? "—")
        }
        .font(.system(size: 11, design: .monospaced))
        .monospacedDigit()
        .foregroundStyle(.secondary)
      }

      Text(Format.shortAge(entry.requestedAt, relativeTo: now))
        .font(.system(size: 10, design: .monospaced))
        .monospacedDigit()
        .foregroundStyle(.tertiary)
        .frame(width: 26, alignment: .trailing)
    }
    .frame(height: 18)
    .padding(.horizontal, 10)
  }
}
