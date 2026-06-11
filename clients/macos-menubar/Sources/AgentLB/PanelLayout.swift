import Foundation

/// §9.1 deterministic panel sizing.
///
/// macOS 26's `MenuBarExtra(.window)` panel measures its content once and
/// never re-measures, and geometry-feedback resizing (measure squeeze →
/// mutate window) proved order-dependent across machines. So the panel
/// height is a PURE FUNCTION of app state: every section renders at a height
/// enforced by an explicit `.frame(height:)` using these metrics, RootView
/// pins its content to the computed total, and PanelResizer applies the same
/// number to the NSWindow. SwiftUI and AppKit can never disagree, and there
/// is no measurement loop to race.
enum PanelMetrics {
  static let width: Double = 460
  static let maxHeight: Double = 720

  // Header: 12 top pad + line 1 (28) + 6 spacing + line 2 (16) + 10 bottom.
  static let headerLine1: Double = 28
  static let headerLine2: Double = 16
  static let header: Double = 12 + headerLine1 + 6 + headerLine2 + 10  // 72

  // Provider scope bar: 26 pt control + 10 pt bottom padding.
  static let scopeControl: Double = 26
  static let scopeBar: Double = scopeControl + 10  // 36

  static let divider: Double = 1
  static let sectionPad: Double = 12  // 6 above + 6 below each section
  static let degradedBanner: Double = 24

  // Pool section internals (VStack spacing 8).
  static let poolLabel: Double = 14
  static let poolSpacing: Double = 8
  static let poolCard: Double = 128
  static let metricsLine: Double = 14
  static let metricsSpacing: Double = 3

  static let retryRow: Double = 16

  // Accounts section internals (VStack spacing 6).
  static let accountsHeader: Double = 22
  static let accountsSpacing: Double = 6
  static let searchField: Double = 24
  static let accountRow: Double = 52
  static let listPadding: Double = 8
  static let maxListRows = 8
  static let emptyState: Double = 110
  static let noMatches: Double = 76

  // Recent section internals (VStack spacing 6).
  static let recentHeader: Double = 16
  static let recentRow: Double = 18
  static let recentCardPadding: Double = 8
  static let recentEmptyLine: Double = 16

  // Footer: 12 + controls row (28) + 12.
  static let footerControls: Double = 28
  static let footer: Double = 12 + footerControls + 12  // 52

  static let serviceDown: Double = 252
  static let loading: Double = 434
}

struct PanelLayout: Equatable {
  /// Total panel/window height (≤ PanelMetrics.maxHeight).
  let panelHeight: Double
  /// Rows visible in the accounts list before it scrolls.
  let listRows: Int
  /// Exact `.frame(height:)` for the accounts ScrollView (0 when the
  /// section shows an empty/error block instead of the list).
  let listHeight: Double

  struct Inputs: Equatable {
    enum Mode: Equatable {
      case content, loading, serviceDown
    }

    var mode: Mode = .content
    var degraded = false
    var showsScopeBar = true
    /// 1 or 2 — second metrics line present when token totals exist.
    var metricsLines = 1
    var poolHasError = false
    /// False only when there is neither a summary nor a provider scope to
    /// recompute from (the pool section then collapses to a retry row).
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

    // §9.1: up to 8 rows, but never more than the 720 pt budget allows —
    // the deterministic version of "the panel cap compresses the list".
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

  // MARK: - Sections

  private static func poolHeight(_ input: Inputs) -> Double {
    guard input.poolHasData else {
      // Retry-only branch (error before any data, scope == all).
      return PanelMetrics.sectionPad + PanelMetrics.poolLabel + PanelMetrics.poolSpacing
        + PanelMetrics.retryRow
    }
    var height = PanelMetrics.sectionPad + PanelMetrics.poolLabel
      + PanelMetrics.poolSpacing + PanelMetrics.poolCard + PanelMetrics.poolSpacing
    height += Double(input.metricsLines) * PanelMetrics.metricsLine
      + Double(input.metricsLines - 1) * PanelMetrics.metricsSpacing
    if input.poolHasError {
      height += PanelMetrics.poolSpacing + PanelMetrics.retryRow
    }
    return height
  }

  /// Accounts section minus the list/empty block itself.
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
