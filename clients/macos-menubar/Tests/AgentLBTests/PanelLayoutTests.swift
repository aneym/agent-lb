import XCTest

@testable import AgentLB









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



  func testServiceDown() {

    let layout = PanelLayout.compute(inputs(mode: .serviceDown))
    XCTAssertEqual(layout.panelHeight, 378)
    XCTAssertEqual(layout.listRows, 0)
    XCTAssertEqual(layout.listHeight, 0)
  }

  func testLoading() {

    XCTAssertEqual(PanelLayout.compute(inputs(mode: .loading)).panelHeight, 560)
  }

  func testLoadingDegradedAddsBanner() {
    var i = inputs(mode: .loading)
    i.degraded = true
    XCTAssertEqual(PanelLayout.compute(i).panelHeight, 584)
  }





  func testThreeRowsRecentExpanded() {

    let layout = PanelLayout.compute(inputs(filtered: 3))
    XCTAssertEqual(layout.listRows, 3)
    XCTAssertEqual(layout.listHeight, 164)
    XCTAssertEqual(layout.panelHeight, 701)
  }

  func testEightRowsCompressToBudget() {

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


    let layout = PanelLayout.compute(inputs(scoped: 8, filtered: 8, recentExpanded: false))
    XCTAssertEqual(layout.listRows, 5)
    XCTAssertEqual(layout.listHeight, 268)
    XCTAssertEqual(layout.panelHeight, 701)
  }

  func testThreeRowsRecentCollapsed() {

    let layout = PanelLayout.compute(inputs(filtered: 3, recentExpanded: false))
    XCTAssertEqual(layout.listRows, 3)
    XCTAssertEqual(layout.panelHeight, 597)
  }

  func testSearchFieldShrinksTheBudget() {

    let layout = PanelLayout.compute(inputs(scoped: 8, filtered: 8, searchVisible: true))
    XCTAssertEqual(layout.listRows, 2)
    XCTAssertEqual(layout.panelHeight, 679)
  }

  func testSingleMetricsLine() {

    let layout = PanelLayout.compute(
      inputs(metricsLines: 1, scoped: 2, filtered: 2, recentExpanded: false))
    XCTAssertEqual(layout.listRows, 2)
    XCTAssertEqual(layout.panelHeight, 528)
  }






  func testThreeMetricsLines() {

    let layout = PanelLayout.compute(inputs(metricsLines: 3, scoped: 3, filtered: 3))
    XCTAssertEqual(layout.listRows, 3)
    XCTAssertEqual(layout.listHeight, 164)
    XCTAssertEqual(layout.panelHeight, 718)
  }

  func testThreeMetricsLinesCompressEight() {

    let layout = PanelLayout.compute(inputs(metricsLines: 3, scoped: 8, filtered: 8))
    XCTAssertEqual(layout.listRows, 3)
    XCTAssertEqual(layout.panelHeight, 718)
  }

  func testThreeMetricsLinesWithSearch() {

    let layout = PanelLayout.compute(
      inputs(metricsLines: 3, scoped: 8, filtered: 8, searchVisible: true))
    XCTAssertEqual(layout.listRows, 2)
    XCTAssertEqual(layout.listHeight, 112)
    XCTAssertEqual(layout.panelHeight, 696)
  }

  func testThreeMetricsLinesRecentCollapsed() {

    let layout = PanelLayout.compute(
      inputs(metricsLines: 3, filtered: 3, recentExpanded: false))
    XCTAssertEqual(layout.listRows, 3)
    XCTAssertEqual(layout.listHeight, 164)
    XCTAssertEqual(layout.panelHeight, 614)
  }



  func testZeroAccountsShowsEmptyState() {

    let layout = PanelLayout.compute(inputs(scoped: 0, filtered: 0))
    XCTAssertEqual(layout.listRows, 0)
    XCTAssertEqual(layout.listHeight, 0)
    XCTAssertEqual(layout.panelHeight, 647)
  }

  func testFilteredToZeroShowsNoMatches() {

    XCTAssertEqual(PanelLayout.compute(inputs(scoped: 3, filtered: 0)).panelHeight, 613)
  }



  func testNeverExceedsMaxHeight() {
    for metricsLines in [1, 2, 3] {
      for filtered in [0, 1, 3, 8, 20] {
        for expanded in [true, false] {
          for search in [true, false] {
            let layout = PanelLayout.compute(
              inputs(metricsLines: metricsLines, scoped: filtered, filtered: filtered,
                     searchVisible: search, recentExpanded: expanded))
            XCTAssertLessThanOrEqual(layout.panelHeight, PanelMetrics.maxHeight)
            XCTAssertGreaterThan(layout.panelHeight, 300)
          }
        }
      }
    }
  }

  func testAtLeastOneRowWheneverAccountsExist() {


    var i = inputs(scoped: 8, filtered: 8, searchVisible: true)
    i.poolHasError = true
    i.accountsHaveError = true
    i.recentHasError = true
    let layout = PanelLayout.compute(i)
    XCTAssertGreaterThanOrEqual(layout.listRows, 1)
    XCTAssertGreaterThanOrEqual(layout.listHeight, 60)
  }
}
