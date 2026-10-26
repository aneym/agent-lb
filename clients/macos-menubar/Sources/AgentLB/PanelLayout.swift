import Foundation











enum PanelMetrics {
  static let width: Double = 460
  static let maxHeight: Double = 720


  static let headerLine1: Double = 28
  static let headerLine2: Double = 16
  static let header: Double = 12 + headerLine1 + 6 + headerLine2 + 10


  static let scopeControl: Double = 26
  static let scopeBar: Double = scopeControl + 10

  static let divider: Double = 1
  static let sectionPad: Double = 12
  static let degradedBanner: Double = 24


  static let poolLabel: Double = 14
  static let poolSpacing: Double = 8
  static let poolCard: Double = 128
  static let metricsLine: Double = 14
  static let metricsSpacing: Double = 3
  static let pacePadding: Double = 10
  static let paceHeader: Double = 12
  static let paceHeaderGap: Double = 6
  static let paceRow: Double = 14
  static let paceRowSpacing: Double = 4

  static func paceCard(rows: Int) -> Double {
    2 * pacePadding + paceHeader + paceHeaderGap
      + Double(rows) * paceRow + Double(max(0, rows - 1)) * paceRowSpacing
  }

  static let retryRow: Double = 16


  static let accountsHeader: Double = 22
  static let accountsSpacing: Double = 6
  static let searchField: Double = 24
  static let accountRow: Double = 52
  static let listPadding: Double = 8
  static let maxListRows = 8
  static let emptyState: Double = 110
  static let noMatches: Double = 76


  static let recentHeader: Double = 16
  static let recentRow: Double = 18
  static let recentCardPadding: Double = 8
  static let recentEmptyLine: Double = 16


  static let footerControls: Double = 28
  static let footer: Double = 12 + footerControls + 12

  static let serviceDown: Double = 252
  static let loading: Double = 434
}

struct PanelLayout: Equatable {

  let panelHeight: Double

  let listRows: Int


  let listHeight: Double

  struct Inputs: Equatable {
    enum Mode: Equatable {
      case content, loading, serviceDown
    }

    var mode: Mode = .content
    var degraded = false
    var showsScopeBar = true

    var metricsLines = 1
    var paceRows = 0
    var poolHasError = false


    var poolHasData = true
    var scopedAccountCount = 0
    var filteredAccountRows = 0
    var searchVisible = false
    var accountsHaveError = false
    var recentExpanded = true
    var recentRows = 0
    var recentHasError = false
  }

  static func compute(_ input: Inputs) -> PanelLayout {
    switch input.mode {
    case .serviceDown:
      return fixed(
        PanelMetrics.header + PanelMetrics.divider + PanelMetrics.serviceDown
          + PanelMetrics.divider + PanelMetrics.footer
      )
    case .loading:
      var height = PanelMetrics.header + PanelMetrics.divider + PanelMetrics.loading
        + PanelMetrics.divider + PanelMetrics.footer
      if input.degraded { height += PanelMetrics.degradedBanner }
      return fixed(height)
    case .content:
      return contentLayout(input)
    }
  }

  private static func fixed(_ height: Double) -> PanelLayout {
    PanelLayout(
      panelHeight: min(height, PanelMetrics.maxHeight),
      listRows: 0,
      listHeight: 0
    )
  }

  private static func contentLayout(_ input: Inputs) -> PanelLayout {
    var base = PanelMetrics.header
    if input.showsScopeBar { base += PanelMetrics.scopeBar }
    base += PanelMetrics.divider
    if input.degraded { base += PanelMetrics.degradedBanner }
    base += poolHeight(input)
    base += PanelMetrics.divider
    base += accountsFixedHeight(input)
    base += PanelMetrics.divider
    base += recentHeight(input)
    base += PanelMetrics.divider
    base += PanelMetrics.footer

    guard input.filteredAccountRows > 0 else {
      return PanelLayout(
        panelHeight: min(base + emptyBlockHeight(input), PanelMetrics.maxHeight),
        listRows: 0,
        listHeight: 0
      )
    }



    let cap = min(input.filteredAccountRows, PanelMetrics.maxListRows)
    let budget = PanelMetrics.maxHeight - base - PanelMetrics.listPadding
    let fitting = Int(budget / PanelMetrics.accountRow)
    let rows = max(1, min(cap, fitting))
    let listHeight = Double(rows) * PanelMetrics.accountRow + PanelMetrics.listPadding
    return PanelLayout(
      panelHeight: min(base + listHeight, PanelMetrics.maxHeight),
      listRows: rows,
      listHeight: listHeight
    )
  }



  private static func poolHeight(_ input: Inputs) -> Double {
    guard input.poolHasData else {

      return PanelMetrics.sectionPad + PanelMetrics.poolLabel + PanelMetrics.poolSpacing
        + PanelMetrics.retryRow
    }
    var height = PanelMetrics.sectionPad + PanelMetrics.poolLabel
      + PanelMetrics.poolSpacing + PanelMetrics.poolCard + PanelMetrics.poolSpacing
    if input.paceRows > 0 {
      height += PanelMetrics.paceCard(rows: input.paceRows) + PanelMetrics.poolSpacing
    }
    height += Double(input.metricsLines) * PanelMetrics.metricsLine
      + Double(input.metricsLines - 1) * PanelMetrics.metricsSpacing
    if input.poolHasError {
      height += PanelMetrics.poolSpacing + PanelMetrics.retryRow
    }
    return height
  }


  private static func accountsFixedHeight(_ input: Inputs) -> Double {
    var height = PanelMetrics.sectionPad + PanelMetrics.accountsHeader
      + PanelMetrics.accountsSpacing
    if input.searchVisible {
      height += PanelMetrics.searchField + PanelMetrics.accountsSpacing
    }
    if input.accountsHaveError && input.filteredAccountRows > 0 {
      height += PanelMetrics.accountsSpacing + PanelMetrics.retryRow
    }
    return height
  }

  private static func emptyBlockHeight(_ input: Inputs) -> Double {
    if input.scopedAccountCount == 0 {
      return input.accountsHaveError ? PanelMetrics.retryRow : PanelMetrics.emptyState
    }
    return PanelMetrics.noMatches
  }

  private static func recentHeight(_ input: Inputs) -> Double {
    var height = PanelMetrics.sectionPad + PanelMetrics.recentHeader
    guard input.recentExpanded else { return height }
    height += PanelMetrics.accountsSpacing
    if input.recentRows == 0 {
      height += input.recentHasError ? PanelMetrics.retryRow : PanelMetrics.recentEmptyLine
    } else {
      height += Double(min(input.recentRows, 5)) * PanelMetrics.recentRow
        + PanelMetrics.recentCardPadding
      if input.recentHasError {
        height += PanelMetrics.accountsSpacing + PanelMetrics.retryRow
      }
    }
    return height
  }
}
