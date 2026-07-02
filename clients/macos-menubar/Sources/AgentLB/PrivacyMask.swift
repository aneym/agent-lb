import SwiftUI

/// §12 privacy mode. When enabled, identity-revealing text — account emails /
/// display names, duplicate-disambiguation tags, and the remote host — is
/// replaced with stable, provider-scoped pseudonyms so the panel is safe to
/// screenshot and share publicly. Aggregate pool numbers (usage percentages,
/// cost, the §11 value multiple) are NOT redacted: they are the point of a
/// public post; only *who* is hidden.
struct PrivacyMask: Equatable, Sendable {
  let enabled: Bool
  /// accountId → pseudonym ("Claude 1", "Codex 2"). Stable across refreshes,
  /// sorts, and provider scoping because it is keyed on a fixed sort of the
  /// full account list, never on a row's on-screen position.
  private let names: [String: String]

  static let disabled = PrivacyMask(enabled: false, names: [:])

  /// Builds the pseudonym map from the full account list. Numbering is
  /// per-provider and assigned in accountId order, so a given account keeps
  /// its label for as long as it exists.
  static func build(enabled: Bool, accounts: [Account]) -> PrivacyMask {
    guard enabled else { return .disabled }
    var names: [String: String] = [:]
    var counters: [String: Int] = [:]
    for account in accounts.sorted(by: { $0.accountId < $1.accountId }) {
      let base = providerLabel(account.provider)
      let next = (counters[base] ?? 0) + 1
      counters[base] = next
      names[account.accountId] = "\(base) \(next)"
    }
    return PrivacyMask(enabled: true, names: names)
  }

  /// The name to show for an account: its pseudonym when redacting (a generic
  /// provider label if the account is somehow absent from the map), otherwise
  /// the caller's real display string.
  func name(for account: Account, real: @autoclosure () -> String) -> String {
    guard enabled else { return real() }
    return names[account.accountId] ?? Self.providerLabel(account.provider)
  }

  /// The pseudonym for an accountId alone (tooltips that only carry the id).
  func name(forId accountId: String, provider: String, real: @autoclosure () -> String) -> String {
    guard enabled else { return real() }
    return names[accountId] ?? Self.providerLabel(provider)
  }

  /// Redacts a host label to a generic token when enabled.
  func host(_ real: String) -> String {
    enabled ? "remote" : real
  }

  private static func providerLabel(_ provider: String) -> String {
    switch provider.lowercased() {
    case "anthropic": return "Claude"
    case "openai": return "Codex"
    default: return "Account"
    }
  }
}

private struct PrivacyMaskKey: EnvironmentKey {
  static let defaultValue = PrivacyMask.disabled
}

extension EnvironmentValues {
  var privacyMask: PrivacyMask {
    get { self[PrivacyMaskKey.self] }
    set { self[PrivacyMaskKey.self] = newValue }
  }
}
