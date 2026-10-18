import SwiftUI

struct SeatAccountRow: View {
  @Environment(\.privacyMask) private var privacyMask
  let seat: SeatAccount
  var now: Date = .now

  var body: some View {
    HStack(alignment: .center, spacing: 9) {
      RowRing(percent: seat.primaryQuotaWindow?.remainingPercent, presentation: presentation)
      VStack(alignment: .leading, spacing: 4) {
        HStack(spacing: 6) {
          Text(displayName)
            .font(.system(size: 13))
            .lineLimit(1)
            .truncationMode(.middle)
          PlanChip(text: seat.planLabel ?? seat.tier)
          if seat.quota?.stale == true {
            PlanChip(text: "stale")
          }
          Spacer(minLength: 6)
          RowTrailing(presentation: presentation, text: trailingText)
        }
        RowWindowGrid(cells: Self.cells(for: seat), now: now, emptyText: emptyText)
      }
    }
    .frame(height: 52)
    .padding(.horizontal, 10)
    .opacity(presentation.isDimmed ? 0.55 : 1)
    .contentShape(.rect)
    .contextMenu {
      Button("Copy Seat ID") {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(seat.id, forType: .string)
      }
    }
    .help(help)
  }

  var presentation: RowPresentation {
    RowPresentation.forSeat(seat)
  }

  var displayName: String {
    privacyMask.name(for: seat, real: seat.id)
  }

  var trailingText: String? {
    if seat.onlyCursorOtherLimited, presentation == .active,
      let reset = seat.cooldowns?["cursor-other"] ?? seat.quotaWindows.first(where: { $0.id == "cursor-other" })?.resetsAt {
      return "Other out until \(Format.easternDay(reset))"
    }
    return presentation.label(now: now, activeText: Self.todayText(seat)) {
      Format.easternMoment($0, relativeTo: now)
    }
  }

  var emptyText: String {
    let cutoff = now.addingTimeInterval(-7 * 24 * 60 * 60)
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = TimeZone(secondsFromGMT: 0)
    formatter.dateFormat = "yyyy-MM-dd"
    let firstDay = formatter.string(from: cutoff)
    let lastDay = formatter.string(from: now)
    let runs = (seat.daily ?? [:]).filter { $0.key > firstDay && $0.key <= lastDay }
      .values.reduce(0) { $0 + $1.values.reduce(0) { $0 + $1.runs } }
    var text = "\(runs) \(runs == 1 ? "run" : "runs") · 7d"
    if let lastRun = seat.lastRunAt {
      text += " · last run \(Format.easternTime(lastRun))"
    }
    return text
  }

  private var help: String {
    var parts = [seat.vendorLabel]
    if let email = privacyMask.email(for: seat) { parts.append(email) }
    if let observed = seat.quota?.observedAt {
      parts.append("usage as of \(Format.easternDateTime(observed)) ET")
    }
    if let models = seat.today?.models, !models.isEmpty {
      parts.append(models.map { "\($0.model) \($0.runs)" }.joined(separator: ", "))
    }
    return parts.joined(separator: " · ")
  }

  static func todayText(_ seat: SeatAccount) -> String? {
    let runs = seat.todayRuns
    guard runs > 0 || seat.today?.valueUsd != nil else { return nil }
    var text = "\(runs) \(runs == 1 ? "run" : "runs")"
    if let value = seat.today?.valueUsd {
      text += " · " + (seat.today?.valueSource == "estimate" ? "~" : "") + Format.dollars(value)
    }
    return text
  }

  static func cells(for seat: SeatAccount) -> [RowWindow] {
    seat.quotaWindows.map { window in
      RowWindow(
        id: window.id,
        label: shortLabel(window),
        percent: window.remainingPercent,
        knownFull: (window.remainingPercent ?? 0) >= 100,
        resetAt: window.resetsAt,
        help: [
          window.label,
          window.remainingPercent.map { "\(Format.percent($0)) left" },
          window.resetsAt.map { "resets \(Format.easternDateTime($0)) ET" },
          window.source == "estimate" ? "estimate" : nil,
        ].compactMap { $0 }.joined(separator: " · ")
      )
    }
  }

  static func shortLabel(_ window: SeatQuotaWindow) -> String {
    switch window.id {
    case "cursor-models": return "CURSOR"
    case "cursor-other": return "OTHER"
    default: break
    }
    switch window.kind {
    case "session": return window.durationMins == 300 ? "5H" : "SES"
    case "daily": return "DAY"
    case "weekly": return "WK"
    case "monthly": return "MO"
    default: return String(window.kind.prefix(3)).uppercased()
    }
  }
}
