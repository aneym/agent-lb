import AppKit
import SwiftUI

struct ResetCreditInventory: Decodable {
  let availableCount: Int
  let credits: [ResetGrant]
}

struct ResetGrant: Decodable, Identifiable {
  let id: String
  let status: String
  let title: String?
  let expiresAt: String?
  let grantedAt: String?
  let clears: [String]?
  let remainingCount: Int?
  let redeemableNow: Bool?
  let ineligibleReason: String?

  var kind: String {
    guard let clears, !clears.isEmpty else { return "Unknown-scope reset" }
    if clears.contains(where: { !["five_hour", "seven_day", "seven_day_overage_included"].contains($0) }) {
      return "Unknown-scope reset"
    }
    if clears.contains("seven_day") { return "Account reset" }
    if clears.contains("seven_day_overage_included") { return "Fable reset" }
    return "5-hour reset"
  }

  var countLabel: String {
    let count = remainingCount ?? (status == "available" ? 1 : 0)
    return status == "available" ? "\(count) banked" : "\(count) unusable (not banked)"
  }

  private func date(_ raw: String?) -> Date? {
    guard let raw else { return nil }
    return Format.iso8601Frac.date(from: raw) ?? Format.iso8601.date(from: raw)
  }

  var blocker: String {
    if let end = date(expiresAt), end <= .now { return "Grant has expired" }
    if let start = date(grantedAt), start > .now { return "Grant has not started yet" }
    return providerBlocker
  }

  private var providerBlocker: String {
    switch ineligibleReason {
    case "provider_ineligible": "Provider says this account is ineligible"
    case "provider_cooldown": "Provider cooldown; wait before trying again"
    case "no_selected_grant": "Provider has not selected this grant for redemption"
    case "not_at_limit": "Account has not reached a limit covered by this grant"
    case "grant_unavailable": "Grant paused, depleted or not currently usable"
    case "grant_not_started": "Grant has not started yet"
    case "grant_expired": "Grant has expired"
    case "grant_cannot_clear_limit": "Grant does not cover the exhausted windows"
    default: ineligibleReason ?? status
    }
  }

  static func windowName(_ key: String) -> String {
    switch key {
    case "five_hour": "5-hour window"
    case "seven_day": "All-models weekly"
    case "seven_day_overage_included": "Fable weekly"
    default: "Unknown window (\(key))"
    }
  }

  var scope: String {
    guard let clears, !clears.isEmpty else { return "Affected windows unknown" }
    return clears.map(Self.windowName).joined(separator: ", ")
  }
}

@MainActor
enum ResetCreditsWindow {
  private static var window: NSWindow?

  static func show(account: Account? = nil, appState: AppState) {
    if let window, window.isVisible {
      window.makeKeyAndOrderFront(nil)
      NSApp.activate(ignoringOtherApps: true)
      return
    }
    let view = ResetCreditsView(initialAccountId: account?.accountId).environment(appState)
    let controller = NSHostingController(rootView: view)
    let panel = NSWindow(contentViewController: controller)
    panel.title = "Agent LB — Reset Credits"
    panel.styleMask = [.titled, .closable, .resizable, .miniaturizable]
    panel.setContentSize(NSSize(width: 660, height: 640))
    panel.isReleasedWhenClosed = false
    window?.close()
    window = panel
    panel.center()
    panel.makeKeyAndOrderFront(nil)
    NSApp.activate(ignoringOtherApps: true)
  }
}

struct ResetCreditsView: View {
  @Environment(AppState.self) private var appState
  let initialAccountId: String?
  @State private var accountId = ""
  @State private var inventory: ResetCreditInventory?
  @State private var busy = false
  @State private var loading = false
  @State private var message: String?
  @State private var selected: ResetGrant?
  @State private var overrideLimits = false
  @State private var confirming = false

  private var account: Account? { appState.accounts.first { $0.accountId == accountId } }
  private var claude: Bool { account?.provider.lowercased() == "anthropic" }

  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      Text("Reset Credits").font(.title2.bold())
      Picker("Account", selection: $accountId) {
        Text("Choose account").tag("")
        ForEach(appState.accounts.filter { ["anthropic", "openai"].contains($0.provider.lowercased()) }) {
          Text($0.displayName).tag($0.accountId)
        }
      }
      .disabled(busy || loading)
      if let account {
        usage(account)
        if claude {
          Text("Account and Fable resets are different. A Fable-only credit does not zero the all-models week. No amount of reclaimed all-models usage is guaranteed.")
            .font(.callout).foregroundStyle(.secondary)
          Toggle("Manual override of local weekly cap and 24-hour limit", isOn: $overrideLimits)
            .disabled(busy)
          Text("Automatic allowance is unchanged. Ownership, subscription, 95% all-models weekly usage, provider eligibility and unresolved-attempt guards still apply.")
            .font(.caption).foregroundStyle(.secondary)
        }
      }
      if loading { ProgressView("Loading grant eligibility…") }
      grantsList
      Button("Refresh Usage and Grants") { Task { await load() } }
        .disabled(busy || loading || accountId.isEmpty)
    }
    .padding(22)
    .frame(minWidth: 600, minHeight: 540)
    .task {
      if appState.accounts.isEmpty { await appState.fetchAccounts() }
      accountId = initialAccountId ?? appState.accounts.first(where: { $0.provider == "anthropic" })?.accountId ?? ""
      await load()
    }
    .onChange(of: accountId) { _, _ in
      overrideLimits = false
      selected = nil
      confirming = false
      Task { await load() }
    }
    .alert("Spend 1 \(selected?.kind ?? "reset") credit?", isPresented: $confirming) {
      Button("Cancel", role: .cancel) {}
      Button(overrideLimits && claude ? "Override and Spend 1 Credit" : "Spend 1 Credit") {
        if let selected { Task { await redeem(selected) } }
      }
    } message: {
      Text("Account: \(account?.displayName ?? accountId) (\(accountId)).\nGrant: \(selected?.title ?? selected?.id ?? "unknown").\nType: \(selected?.kind ?? "unknown"). Affected windows: \(selected?.scope ?? "unknown"). "
        + (selected?.kind == "Fable reset" ? "Fable-only resets do NOT reset the entire all-models week. " : "")
        + (overrideLimits && claude ? "This action bypasses local weekly and 24-hour limits once; automatic allowance is unchanged." : "Local reset caps apply."))
    }
  }

  private var grantsList: some View {
    ScrollView {
        VStack(alignment: .leading, spacing: 14) {
          if let inventory {
            Text("\(inventory.availableCount) banked · eligibility shown per grant").font(.headline)
            ForEach(inventory.credits) { grant in
              grantCard(grant)
              Divider()
            }
          }
          if busy { ProgressView("Resetting… do not retry") }
          if let message {
            Text(message).textSelection(.enabled).accessibilityIdentifier("reset-result")
          }
        }
      }
  }

  private func grantCard(_ grant: ResetGrant) -> some View {
              VStack(alignment: .leading, spacing: 5) {
                Text("\(grant.kind) · \(grant.countLabel)").bold()
                Text(grant.title ?? grant.id).font(.caption).foregroundStyle(.secondary)
                Text(grant.scope)
                Text("Expires: \(grant.expiresAt ?? "not supplied")").font(.caption)
                Text(grant.redeemableNow == true ? "Provider eligible now · local policy checked on submission" : "Not eligible now: \(grant.blocker)")
                  .font(.caption)
                Button("Use \(grant.kind)…") { selected = grant; confirming = true }
                  .disabled(busy || (claude ? grant.redeemableNow != true : grant.status != "available"))
              }
              .frame(maxWidth: .infinity, alignment: .leading)
  }

  private func usage(_ account: Account) -> some View {
    VStack(alignment: .leading, spacing: 4) {
      Text("All-models weekly: \(used(account.usage.secondaryRemainingPercent.map { 100 - $0 }))")
      if claude { Text("Fable weekly: \(used(fableUsed(account)))") }
      Text("5-hour window: \(used(account.usage.primaryRemainingPercent.map { 100 - $0 }))")
    }.monospacedDigit()
  }

  private func fableUsed(_ account: Account?) -> Double? {
    account?.additionalQuotas?.first { $0.quotaKey == "anthropic_fable_scoped_weekly" }?.primaryWindow?.usedPercent
  }

  private func used(_ value: Double?) -> String {
    value.map { String(format: "%.1f%% used", $0) } ?? "unknown"
  }

  private func load() async {
    guard !accountId.isEmpty, !busy, !loading else { return }
    message = nil
    loading = true
    inventory = nil
    let id = accountId
    await appState.fetchAccounts()
    defer { loading = false }
    do {
      let result = try await APIClient().resetCredits(id)
      if id == accountId { inventory = result }
    } catch { message = errorMessage(error) }
  }

  private func redeem(_ grant: ResetGrant) async {
    guard !busy else { return }
    busy = true
    let beforeFable = fableUsed(account)
    defer { busy = false; overrideLimits = false }
    do {
      let result = try await appState.redeemResetCredit(accountId: accountId, creditId: grant.id, overrideLimits: overrideLimits && claude)
      await appState.fetchAccounts()
      var lines = ["\(result.status): \(result.code)."]
      let cleared = result.clearedWindows ?? []
      lines.append("Actual cleared windows: " + (cleared.isEmpty ? "not reported (\(result.windowsReset) windows)" : cleared.map(ResetGrant.windowName).joined(separator: ", ")))
      lines.append("Observed all-models weekly: \(used(result.secondaryUsedPercentBefore)) → \(used(result.secondaryUsedPercentAfter))")
      lines.append("Observed 5-hour: \(used(result.primaryUsedPercentBefore)) → \(used(result.primaryUsedPercentAfter))")
      if claude { lines.append("Observed Fable weekly: \(used(beforeFable)) → \(used(fableUsed(account)))") }
      lines.append("After readings are observed stored values, not guaranteed refreshed. Unchanged limits may still apply. No automatic retry; refresh and reconcile before another action.")
      message = lines.joined(separator: "\n")
      inventory = nil
    } catch { message = errorMessage(error) }
  }

  private func errorMessage(_ error: any Error) -> String {
    if case APIError.httpStatus(let code, let detail) = error {
      return "Reset request refused (HTTP \(code)): \(detail ?? "No server detail. Check service logs.")"
    }
    return "Could not confirm the operation. Refresh and reconcile before retrying; a credit may already have been used."
  }
}
