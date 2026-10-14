import Foundation















enum PlanPricing {



  static func monthlyUSD(provider: String, planType: String?) -> Double? {
    let plan = (planType ?? "").lowercased()
    switch provider.lowercased() {
    case "anthropic":
      switch plan {



      case "max", "claude": return 200
      case "max5", "max_5x": return 100
      case "pro": return 20
      case "free": return 0
      default: return nil
      }
    case "openai":
      switch plan {
      case "pro": return 200
      case "plus": return 20
      case "free": return 0
      default: return nil
      }
    default:
      return nil
    }
  }





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





struct ArbitrageStats: Equatable, Sendable {

  let valueUSD: Double

  let monthlyPlanUSD: Double

  let weeklyPlanUSD: Double

  let planCount: Int

  let multiple: Double

  let estimated: Bool



  static let daysPerMonth = 30.4375



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



  var multipleLabel: String {
    (estimated ? "≈" : "") + Format.multiple(multiple)
  }
}
