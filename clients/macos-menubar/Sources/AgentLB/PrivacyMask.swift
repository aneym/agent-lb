import SwiftUI







struct PrivacyMask: Equatable, Sendable {
  let enabled: Bool



  private let names: [String: String]

  static let disabled = PrivacyMask(enabled: false, names: [:])




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




  func name(for account: Account, real: @autoclosure () -> String) -> String {
    guard enabled else { return real() }
    return names[account.accountId] ?? Self.providerLabel(account.provider)
  }


  func name(forId accountId: String, provider: String, real: @autoclosure () -> String) -> String {
    guard enabled else { return real() }
    return names[accountId] ?? Self.providerLabel(provider)
  }


  func sharedChipText(for account: Account) -> String? {
    guard let source = account.sharedFrom else { return nil }
    return enabled ? "SHARED" : "VIA \(source.uppercased())"
  }

  func sharedChipHelp(for account: Account) -> String? {
    guard let source = account.sharedFrom else { return nil }
    if enabled { return "Shared account. This LB only routes on it." }
    return "Shared from \(source)'s agent-lb. It owns and refreshes this account; this LB only routes on it."
  }


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
