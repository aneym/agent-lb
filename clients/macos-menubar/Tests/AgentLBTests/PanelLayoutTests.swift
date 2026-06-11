import XCTest

@testable import AgentLB

/// Deterministic §9.1 panel sizing. Expectations are HAND-COMPUTED from
/// PanelMetrics (never derived by re-running the production formula):
///
///   header 72 · scope bar 36 · divider 1 · footer 52
///   pool   = 12 + 14 + 8 + 128 + 8 + metrics(14 | 31)        → 184 | 201
///   accounts fixed = 12 + 22 + 6 (+30 search)                → 40 | 70
///   recent = 12 + 16 (+6 + rows×18 + 8 when expanded)        → 28 | 132@5
///   list   = rows × 52 + 8
final class PanelLayoutTests: XCTestCase {

  private func inputs(
    mode: PanelLayout.Inputs.Mode = .content,
    metricsLines: Int = 2,
    scoped: Int = 3,
    filtered: Int = 3,
    searchVisible: Bool = false,
    recentExpanded: Bool = true,
    recentRows: Int = 5
  ) -> PanelLayout.Inputs {
    var inputs = PanelLayout.Inputs()
    inputs.mode = mode
    inputs.metricsLines = metricsLines
    inputs.scopedAccountCount = scoped
    inputs.filteredAccountRows = filtered
    inputs.searchVisible = searchVisible
    inputs.recentExpanded = recentExpanded
    inputs.recentRows = recentRows
    return inputs
  }

  // MARK: - Fixed modes

  func testServiceDown() {
    // 72 + 1 + 252 + 1 + 52
    let layout = PanelLayout.compute(inputs(mode: .serviceDown))
    XCTAssertEqual(layout.panelHeight, 378)
    XCTAssertEqual(layout.listRows, 0)
    XCTAssertEqual(layout.listHeight, 0)
  }

  func testLoading() {
    // 72 + 1 + 434 + 1 + 52
    XCTAssertEqual(PanelLayout.compute(inputs(mode: .loading)).panelHeight, 560)
  }

  func testLoadingDegradedAddsBanner() {
    var i = inputs(mode: .loading)
    i.degraded = true
    XCTAssertEqual(PanelLayout.compute(i).panelHeight, 584)
  }

  // MARK: - Content: row counts vs budget
  // base(recent expanded, 5 recent rows, 2 metric lines)
  //   = 72+36+1 + 201 + 1 + 40 + 1 + 132 + 1+52 = 537

  func testThreeRowsRecentExpanded() {
    // budget = 720-537-8 = 175 → 3 rows fit → list 164 → 537+164 = 701
    let layout = PanelLayout.compute(inputs(filtered: 3))
    XCTAssertEqual(layout.listRows, 3)
    XCTAssertEqual(layout.listHeight, 164)
    XCTAssertEqual(layout.panelHeight, 701)
  }

  func testEightRowsCompressToBudget() {
    // 8 filtered, but only 3 fit under the 720 cap with recent expanded
    let layout = PanelLayout.compute(inputs(scoped: 8, filtered: 8))
    XCTAssertEqual(layout.listRows, 3)
    XCTAssertEqual(layout.panelHeight, 701)
  }

  func testNinePlusRowsCapAtEightThenBudget() {
    let layout = PanelLayout.compute(inputs(scoped: 12, filtered: 12))
    XCTAssertEqual(layout.listRows, 3, "cap at 8 first, then the 720 budget")
    XCTAssertEqual(layout.panelHeight, 701)
  }

  func testRecentCollapsedFreesRowsForTheList() {
    // base(collapsed) = 72+36+1 + 201 + 1 + 40 + 1 + 28 + 53 = 433
    // budget = 720-433-8 = 279 → 5 rows → list 268 → 701
    let layout = PanelLayout.compute(inputs(scoped: 8, filtered: 8, recentExpanded: false))
    XCTAssertEqual(layout.listRows, 5)
    XCTAssertEqual(layout.listHeight, 268)
    XCTAssertEqual(layout.panelHeight, 701)
  }

  func testThreeRowsRecentCollapsed() {
    // 433 + 164 = 597 — panel hugs content well below the cap
    let layout = PanelLayout.compute(inputs(filtered: 3, recentExpanded: false))
    XCTAssertEqual(layout.listRows, 3)
    XCTAssertEqual(layout.panelHeight, 597)
  }

  func testSearchFieldShrinksTheBudget() {
    // base 537+30 = 567 → budget 145 → 2 rows → list 112 → 679
    let layout = PanelLayout.compute(inputs(scoped: 8, filtered: 8, searchVisible: true))
    XCTAssertEqual(layout.listRows, 2)
    XCTAssertEqual(layout.panelHeight, 679)
  }

  func testSingleMetricsLine() {
    // pool 184; base = 109+184+1+40+1+28+53 = 416 → +list 112 (2 rows) = 528
    let layout = PanelLayout.compute(
      inputs(metricsLines: 1, scoped: 2, filtered: 2, recentExpanded: false))
    XCTAssertEqual(layout.listRows, 2)
    XCTAssertEqual(layout.panelHeight, 528)
  }

  // MARK: - Content: empty blocks

  func testZeroAccountsShowsEmptyState() {
    // 537 + 110 = 647
    let layout = PanelLayout.compute(inputs(scoped: 0, filtered: 0))
    XCTAssertEqual(layout.listRows, 0)
    XCTAssertEqual(layout.listHeight, 0)
    XCTAssertEqual(layout.panelHeight, 647)
  }

  func testFilteredToZeroShowsNoMatches() {
    // 537 + 76 = 613
    XCTAssertEqual(PanelLayout.compute(inputs(scoped: 3, filtered: 0)).panelHeight, 613)
  }

  // MARK: - Invariants

  func testNeverExceedsMaxHeight() {
    for filtered in [0, 1, 3, 8, 20] {
      for expanded in [true, false] {
        for search in [true, false] {
          let layout = PanelLayout.compute(
            inputs(scoped: filtered, filtered: filtered,
                   searchVisible: search, recentExpanded: expanded))
          XCTAssertLessThanOrEqual(layout.panelHeight, PanelMetrics.maxHeight)
          XCTAssertGreaterThan(layout.panelHeight, 300)
        }
      }
    }
  }

  func testAtLeastOneRowWheneverAccountsExist() {
    // Pathological: search open + errors everywhere + recent expanded —
    // the list still shows at least one row rather than vanishing.
    var i = inputs(scoped: 8, filtered: 8, searchVisible: true)
    i.poolHasError = true
    i.accountsHaveError = true
    i.recentHasError = true
    let layout = PanelLayout.compute(i)
    XCTAssertGreaterThanOrEqual(layout.listRows, 1)
    XCTAssertGreaterThanOrEqual(layout.listHeight, 60)
  }
}
