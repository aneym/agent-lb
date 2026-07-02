import Foundation

/// §11 subscription plan pricing.
///
/// The pool's headline vanity metric is the *value multiple*: the
/// API-equivalent dollar value of the tokens the pool burned in the weekly
/// window (`summary.cost.totalUsd7d`, already priced at retail list rates
/// server-side) divided by what those same days of flat-rate subscriptions
/// cost. The value (numerator) is real; the subscription cost (denominator)
/// needs a per-account monthly price.
///
/// That price comes from the operator-entered `subscription.amount` when
/// present; otherwise it falls back to this list-price table keyed on
/// (provider, planType). Any pool that used the fallback for even one account
/// is flagged `estimated` so the UI can mark the multiple approximate ("≈").
/// These are published list prices, not amounts anyone was billed.
enum PlanPricing {
  /// Monthly USD list price for a (provider, planType) pair. Nil when the plan
  /// type has no single consumer price (per-seat / enterprise / unknown): such
  /// accounts are excluded from the denominator rather than guessed at.
  static func monthlyUSD(provider: String, planType: String?) -> Double? {
    let plan = (planType ?? "").lowercased()
    switch provider.lowercased() {
    case "anthropic":
      switch plan {
      // "claude" is this pool's stored label for a Max subscription
      // (ACCOUNT_PLAN_EQUIVALENTS aliases claude↔max server-side); Max 20x is
      // the flagship tier these accounts run.
      case "max", "claude": return 200
      case "max5", "max_5x": return 100
      case "pro": return 20
      case "free": return 0
      default: return nil
      }
    case "openai":
      switch plan {
      case "pro": return 200          // ChatGPT / Codex Pro
      case "plus": return 20
      case "free": return 0
      default: return nil             // team / business / enterprise are per-seat
      }
    default:
      return nil
    }
  }

  /// Effective monthly USD for one account. The operator-entered amount wins
  /// (only when denominated in USD, to keep the ratio single-currency);
  /// otherwise the list-price table, flagged `estimated`. Nil when no price is
  /// known for the account's plan type.
  static func monthlyUSD(for account: Account) -> (amount: Double, estimated: Bool)? {
    if let amount = account.subscription?.amount, amount > 0,
       (account.subscription?.currency ?? "USD").uppercased() == "USD" {
      return (amount, false)
    }
    guard let listed = monthlyUSD(provider: account.provider, planType: account.planType),
          listed > 0
    else { return nil }
    return (listed, true)
  }
}

/// §11 computed arbitrage figures for the pool. Pool-global by construction:
/// the value numerator (`totalUsd7d`) is an all-providers server figure, so
/// the denominator sums every headline-countable account regardless of the
/// menu bar's provider scope.
struct ArbitrageStats: Equatable, Sendable {
  /// API-equivalent retail value of tokens burned in the weekly window (USD).
  let valueUSD: Double
  /// Σ monthly subscription price across the counted plans (USD/month).
  let monthlyPlanUSD: Double
  /// Those subscriptions prorated to the 7-day value window (USD/week).
  let weeklyPlanUSD: Double
  /// Number of accounts that contributed a price to the denominator.
  let planCount: Int
  /// `valueUSD / weeklyPlanUSD` — the headline "N×".
  let multiple: Double
  /// True when any counted plan used the PlanPricing fallback table.
  let estimated: Bool

  /// Average civil days per month (365.25 / 12) — prorates the monthly plan
  /// fee down to the 7-day window `totalUsd7d` measures.
  static let daysPerMonth = 30.4375

  /// Nil when there is no value figure or no priced account — the arbitrage
  /// line then simply does not render.
  static func compute(summary: UsageSummary?, accounts: [Account]) -> ArbitrageStats? {
    guard let value = summary?.cost?.totalUsd7d, value > 0 else { return nil }

    var monthly = 0.0
    var count = 0
    var estimated = false
    for account in accounts where account.isHeadlineCountable {
      guard let priced = PlanPricing.monthlyUSD(for: account) else { continue }
      monthly += priced.amount
      count += 1
      if priced.estimated { estimated = true }
    }
    guard count > 0, monthly > 0 else { return nil }

    let weekly = monthly * 7.0 / daysPerMonth
    guard weekly > 0 else { return nil }
    return ArbitrageStats(
      valueUSD: value,
      monthlyPlanUSD: monthly,
      weeklyPlanUSD: weekly,
      planCount: count,
      multiple: value / weekly,
      estimated: estimated
    )
  }

  /// "≈27×" / "27×" — the headline multiple with an approximation mark when
  /// any plan price was estimated from the list-price table.
  var multipleLabel: String {
    (estimated ? "≈" : "") + Format.multiple(multiple)
  }
}
